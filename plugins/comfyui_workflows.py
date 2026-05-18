import base64
import json
import os
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypedDict

import requests

from cloudbot import hook
from cloudbot.util.web import TimeoutSession, get_session
from plugins.huggingface import FileIrcResponseWrapper

AUDIO_LENGTH = 60


class GlobalLock(TypedDict):
    nick: str
    locked_at: datetime
    expires_at: datetime


@dataclass
class PendingJob:
    """Represents a pending ComfyUI workflow execution."""

    prompt_id: str
    chan: str
    nick: str
    workflow_name: str
    prompt_text: str
    submitted_at: datetime
    network: str


class ComfyUIClient:
    """Client for interacting with ComfyUI API."""

    def __init__(
        self,
        api_url: str,
        username: str | None = None,
        password: str | None = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.username = username
        self.password = password
        self._session: TimeoutSession | None = None

    def _get_session(self) -> TimeoutSession:
        if self._session is None:
            self._session = get_session()
            if self.username and self.password:
                auth_string = f"{self.username}:{self.password}"
                encoded = base64.b64encode(auth_string.encode()).decode()
                self._session.headers.update(
                    {"Authorization": f"Basic {encoded}"}
                )
        return self._session

    def submit_prompt(self, workflow_data: dict) -> str:
        session = self._get_session()
        response = session.post(
            f"{self.api_url}/api/prompt",
            json=workflow_data,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        result = response.json()
        return result.get("prompt_id")

    def get_history(self, prompt_id: str) -> dict | None:
        session = self._get_session()
        response = session.get(f"{self.api_url}/api/history/{prompt_id}")
        response.raise_for_status()
        history = response.json()
        return history.get(prompt_id)

    def download_file(
        self, filename: str, output_type: str = "output", subfolder: str = ""
    ) -> bytes:
        session = self._get_session()
        params = {"filename": filename, "type": output_type}
        if subfolder:
            params["subfolder"] = subfolder

        response = session.get(f"{self.api_url}/api/view", params=params)
        response.raise_for_status()
        return response.content


class WorkflowExecutor:
    """Manages workflow execution and job tracking."""

    def __init__(self, client: ComfyUIClient, config: dict):
        self.client = client
        self.config = config
        self.pending_jobs: deque[PendingJob] = deque()
        self.workflow_template = self._load_workflow_template()

    def _load_workflow_template(self) -> dict:
        """Load the base workflow template for audio generation."""
        return {
            "client_id": "",
            "prompt": {
                "3": {
                    "inputs": {
                        "seed": 564198054889628,
                        "steps": 50,
                        "cfg": 4.98,
                        "sampler_name": "dpmpp_3m_sde_gpu",
                        "scheduler": "exponential",
                        "denoise": 1,
                        "model": ["4", 0],
                        "positive": ["6", 0],
                        "negative": ["7", 0],
                        "latent_image": ["11", 0],
                    },
                    "class_type": "KSampler",
                    "_meta": {"title": "KSampler"},
                },
                "4": {
                    "inputs": {
                        "ckpt_name": "stable-audio-open-1.0.safetensors"
                    },
                    "class_type": "CheckpointLoaderSimple",
                    "_meta": {"title": "Load Checkpoint"},
                },
                "6": {
                    "inputs": {"text": "PLACEHOLDER_PROMPT", "clip": ["10", 0]},
                    "class_type": "CLIPTextEncode",
                    "_meta": {"title": "CLIP Text Encode (Prompt)"},
                },
                "7": {
                    "inputs": {"text": "", "clip": ["10", 0]},
                    "class_type": "CLIPTextEncode",
                    "_meta": {"title": "CLIP Text Encode (Prompt)"},
                },
                "10": {
                    "inputs": {
                        "clip_name": "t5-base.safetensors",
                        "type": "stable_audio",
                        "device": "default",
                    },
                    "class_type": "CLIPLoader",
                    "_meta": {"title": "Load CLIP"},
                },
                "11": {
                    "inputs": {"seconds": AUDIO_LENGTH, "batch_size": 1},
                    "class_type": "EmptyLatentAudio",
                    "_meta": {"title": "EmptyLatentAudio"},
                },
                "12": {
                    "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
                    "class_type": "VAEDecodeAudio",
                    "_meta": {"title": "VAEDecodeAudio"},
                },
                "13": {
                    "inputs": {
                        "filename_prefix": "audio",
                        "audioUI": "",
                        "audio": ["12", 0],
                    },
                    "class_type": "SaveAudio",
                    "_meta": {"title": "SaveAudio"},
                },
            },
        }

    def submit_workflow(
        self,
        workflow_name: str,
        prompt_text: str,
        chan: str,
        nick: str,
        network: str,
    ) -> str:
        workflow_config = self.config.get("workflows", {}).get(workflow_name)
        if not workflow_config:
            return f"❌ Unknown workflow: {workflow_name}"

        workflow_data = json.loads(json.dumps(self.workflow_template))
        workflow_data["prompt"]["6"]["inputs"]["text"] = prompt_text

        try:
            prompt_id = self.client.submit_prompt(workflow_data)

            job = PendingJob(
                prompt_id=prompt_id,
                chan=chan,
                nick=nick,
                workflow_name=workflow_name,
                prompt_text=prompt_text,
                submitted_at=datetime.now(),
                network=network,
            )
            self.pending_jobs.append(job)

            return f"⏳ Generating audio for '{prompt_text[:50]}{'...' if len(prompt_text) > 50 else ''}' (Job ID: {prompt_id[:8]})"

        except requests.exceptions.RequestException as e:
            return f"❌ Failed to submit workflow: {str(e)}"

    def check_pending_jobs(self, bot) -> None:
        max_wait = self.config.get("max_wait_time", 120)
        now = datetime.now()
        completed_jobs = []

        for job in self.pending_jobs:
            if (now - job.submitted_at).total_seconds() > max_wait:
                self._notify_timeout(bot, job)
                completed_jobs.append(job)
                continue

            try:
                history = self.client.get_history(job.prompt_id)
                if history and self._is_job_complete(history):
                    self._process_completed_job(bot, job, history)
                    completed_jobs.append(job)
                elif history and self._is_job_failed(history):
                    self._notify_failure(bot, job, history)
                    completed_jobs.append(job)
            except requests.exceptions.RequestException as e:
                print(f"Error checking job {job.prompt_id}: {e}")

        for job in completed_jobs:
            self.pending_jobs.remove(job)

    def _is_job_complete(self, history: dict) -> bool:
        status = history.get("status", {})
        if not status.get("completed", False):
            return False

        outputs = history.get("outputs", {})
        return bool(outputs)

    def _is_job_failed(self, history: dict) -> bool:
        status = history.get("status", {})
        if not status.get("completed", False):
            return False

        outputs = history.get("outputs", {})
        return not bool(outputs)

    def _process_completed_job(
        self, bot, job: PendingJob, history: dict
    ) -> None:
        try:
            outputs = history.get("outputs", {})

            output_filename = None
            for node_output in outputs.values():
                if "audio" in node_output:
                    audio_data = node_output["audio"]
                    if audio_data:
                        output_filename = audio_data[0]["filename"]
                        break

            if not output_filename:
                self._notify_error(bot, job, "No output file found in results")
                return

            file_content = self.client.download_file(
                output_filename, output_type="output"
            )

            extension = output_filename.split(".")[-1]
            with tempfile.NamedTemporaryFile(
                suffix=f".{extension}", delete=False
            ) as tmp_file:
                tmp_file.write(file_content)
                tmp_path = tmp_file.name

            try:
                upload_url = FileIrcResponseWrapper.upload_file(
                    tmp_path, job.chan
                )
                self._notify_success(bot, job, upload_url)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        except requests.exceptions.RequestException as e:
            self._notify_error(bot, job, str(e))

    def _notify_success(self, bot, job: PendingJob, url: str) -> None:
        _release_lock()
        conn = bot.connections.get(job.network)
        if conn and conn.ready:
            message = f"{job.nick}: Audio generated! {url}"
            conn.message(job.chan, message)

    def _notify_failure(self, bot, job: PendingJob, history: dict) -> None:
        _release_lock()
        conn = bot.connections.get(job.network)
        if conn and conn.ready:
            status = history.get("status", {})
            error_messages = status.get("messages", [])
            error_text = (
                error_messages[0] if error_messages else "Unknown error"
            )
            message = f"{job.nick}: ❌ Audio generation failed: {error_text}"
            conn.message(job.chan, message)

    def _notify_timeout(self, bot, job: PendingJob) -> None:
        _release_lock()
        conn = bot.connections.get(job.network)
        if conn and conn.ready:
            message = (
                f"{job.nick}: ⏰ Audio generation timed out. Please try again."
            )
            conn.message(job.chan, message)

    def _notify_error(self, bot, job: PendingJob, error: str) -> None:
        _release_lock()
        conn = bot.connections.get(job.network)
        if conn and conn.ready:
            message = f"❌ {job.nick}: Error processing result: {error}"
            conn.message(job.chan, message)


_LOCK_DURATION_MINUTES = 3


class _State:
    executor: WorkflowExecutor | None = None
    global_lock: GlobalLock | None = None


def _acquire_lock(nick: str) -> str | None:
    now = datetime.now()
    current_lock = _State.global_lock

    if current_lock is not None:
        if now < current_lock["expires_at"]:
            locked_nick = current_lock["nick"]
            time_left = (current_lock["expires_at"] - now).total_seconds()
            return f"⏳ Audio generation is currently in use by {locked_nick}. Please wait {int(time_left)}s or try again later."
        _State.global_lock = None

    _State.global_lock = GlobalLock(
        nick=nick,
        locked_at=now,
        expires_at=now + timedelta(minutes=_LOCK_DURATION_MINUTES),
    )
    return None


def _release_lock() -> None:
    _State.global_lock = None


@hook.on_start()
def init_comfyui(bot) -> None:
    config = bot.config.get("plugins", {}).get("comfyui_workflows", {})

    if not config.get("api_url"):
        print(
            "ComfyUI workflows plugin: No API URL configured, plugin disabled"
        )
        return

    api_url = config["api_url"]
    basic_auth = config.get("basic_auth", {})
    username = basic_auth.get("username")
    password = basic_auth.get("password")

    client = ComfyUIClient(api_url, username, password)
    _State.executor = WorkflowExecutor(client, config)

    print(f"ComfyUI workflows plugin initialized: {api_url}")


@hook.periodic(2, initial_interval=2)
def check_jobs(bot) -> None:
    if _State.executor:
        _State.executor.check_pending_jobs(bot)


@hook.command("aimusic", autohelp=False)
def aiaudio_cmd(text: str, chan: str, nick: str, conn) -> str:
    if not _State.executor:
        return "❌ ComfyUI workflows plugin not configured"

    if not text or not text.strip():
        return "Usage: .aimusic <prompt> - Example: .aimusic epic orchestral soundtrack"

    lock_error = _acquire_lock(nick)
    if lock_error:
        return lock_error

    prompt = text.strip()
    return _State.executor.submit_workflow(
        "audio_stable_audio_example", prompt, chan, nick, conn.name
    )
