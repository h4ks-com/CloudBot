"""Agentic LLM dispatcher for CloudBot.

Explicit-only entry: .ask / .agent / .agi <prompt>. Routes to an LLM agent
that can call existing bot commands as tools. Uses openai-agents 0.17.1
with Z.AI glm-5 (primary) and OpenRouter (fallback).

Tool definitions live in cloudbot.agent (outside plugins/) so the plugin
manager doesn't try to load them as plugins.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections import deque
from datetime import datetime
from urllib.parse import urlsplit

from agents import Agent, FunctionTool, RunContextWrapper, RunHooks, Runner
from agents.agent import StopAtTools
from agents.exceptions import AgentsException, MaxTurnsExceeded
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_provider import OpenAIProvider
from agents.run import RunConfig
from openai import AsyncOpenAI, BadRequestError, OpenAIError
from sqlalchemy.exc import SQLAlchemyError

from cloudbot import hook
from cloudbot.agent import (
    AGENT_INSTRUCTIONS,
    build_custom_tools,
    fetch_github_username,
    fetch_self_repo_push,
    resolve_config_path,
    sanitise_err_message,
    upload_markdown_paste,
)
from cloudbot.agent.common import (
    memory_read_namespaces,
    recent_chat_snippet,
    run_in_executor,
)
from cloudbot.agent.runs import recent_runs
from cloudbot.agent.skills import skill_index
from cloudbot.agent.tools.kaggle import ensure_kaggle_table, notebook_context
from cloudbot.agent.tools.mcp_servers import (
    build_mcp_tools,
    discover,
    reload_servers,
)
from cloudbot.agent.tools.memory import all_memories, ensure_fts
from cloudbot.event import CommandEvent
from cloudbot.util import web
from cloudbot.util.ai_common import wrap_reply_lines
from cloudbot.util.typing import (
    start_typing_for_command,
    stop_typing_for_command,
)
from plugins.core import bot_cmds

logger = logging.getLogger("cloudbot")

# Curated allow-list of bot commands exposed as agent tools. Filtered against
# privileged permissions and the per-plugin `exclude` config at build time.
DEFAULT_INCLUDE = frozenset(
    {
        "1337",
        "adj",
        "adjrel",
        "amazon",
        "antonym",
        "api",
        "artist",
        "arxiv",
        "arxiv_next",
        "ast",
        "astronomy",
        "aw",
        "awesome",
        "awn",
        "ax",
        "axn",
        "az",
        "b",
        "band",
        "batteryinfo",
        "batterystats",
        "bible",
        "bing",
        "bingimage",
        "bis",
        "bookpun",
        "books",
        "ca",
        "cakeday",
        "calc",
        "cipher",
        "compare",
        "confucious",
        "convert",
        "cool",
        "crypto",
        "cryptocurrency",
        "currencies",
        "currencylist",
        "cypher",
        "dadjoke",
        "ddg",
        "ddg_next",
        "decipher",
        "decypher",
        "define",
        "devdocs",
        "dictionary",
        "dig",
        "directions",
        "doc",
        "docs",
        "documentation",
        "dogpile",
        "doit",
        "domain",
        "domainr",
        "down",
        "dp",
        "dpis",
        "drink",
        "e",
        "etree",
        "etymology",
        "expand",
        "fact",
        "fakenews",
        "fc",
        "fcd",
        "fcw",
        "feed",
        "forecast",
        "forecastweek",
        "fortune",
        "funtranslate",
        "g",
        "gbooks",
        "gd",
        "geoguess",
        "geoip",
        "getlyrics",
        "gh",
        "ghissue",
        "ghn",
        "ghnext",
        "ghpaste",
        "ghsource",
        "gis",
        "github",
        "gitio",
        "gmd",
        "gn",
        "googl",
        "google_translate",
        "gw",
        "gwn",
        "hltb",
        "hltb_next",
        "hltbn",
        "horoscope",
        "howlongtobeat",
        "imdb",
        "imdb_next",
        "imdbn",
        "iscool",
        "isgd",
        "iss",
        "issafe",
        "issue",
        "isup",
        "karma",
        "kernel",
        "kero",
        "kerowhack",
        "l",
        "l33t",
        "la",
        "langlist",
        "last",
        "lastfm",
        "lastfmcompare",
        "lawyerjoke",
        "lc",
        "leet",
        "leetify",
        "libreband",
        "librecompare",
        "librefm",
        "librela",
        "librelast",
        "librelc",
        "librelibrelta",
        "libreltm",
        "libreltw",
        "librenp",
        "libreplays",
        "libreta",
        "libretop",
        "libretopall",
        "libretopartists",
        "libretopmonth",
        "libretoptrack",
        "libretoptracks",
        "libretopweek",
        "librett",
        "locate",
        "lta",
        "ltm",
        "ltop",
        "ltt",
        "ltw",
        "lty",
        "lyn",
        "lyrics",
        "lyricsn",
        "lysearch",
        "next",
        "man",
        "maps",
        "mars",
        "marslocations",
        "marslocs",
        "marstime",
        "math",
        "mc",
        "mcp",
        "mcping",
        "mcstatus",
        "mcwiki",
        "mean",
        "meh",
        "meta",
        "metacritic",
        "metan",
        "moderates",
        "mods",
        "moremod",
        "morse",
        "morsecode",
        "morsetrans",
        "news",
        "newsn",
        "noun",
        "np",
        "octo",
        "octopart",
        "offline",
        "passage",
        "password",
        "pi",
        "pig",
        "piglatin",
        "pkg",
        "pkglist",
        "pkgn",
        "plaudio",
        "play",
        "playn",
        "plays",
        "plimage",
        "playstation",
        "playstore",
        "playstoren",
        "pronounce",
        "psn",
        "psnn",
        "pun",
        "quran",
        "queue",
        "randomword",
        "reddit",
        "rhyme",
        "rhymerel",
        "rinfo",
        "rmods",
        "rottentomatoes",
        "rss",
        "radio",
        "req",
        "reqgse",
        "reqyt",
        "request",
        "rsource",
        "rsuno",
        "rslop",
        "rt",
        "ruser",
        "shorten",
        "sid",
        "slickdeals",
        "so",
        "son",
        "songrec",
        "sounditout",
        "soundlike",
        "spalbum",
        "spartist",
        "spell",
        "spotify",
        "sptrack",
        "stackoverflow",
        "steam",
        "steamcalc",
        "steamdb",
        "steamid",
        "steamuser",
        "stock",
        "streetview",
        "su",
        "sub",
        "subinfo",
        "submods",
        "subreddit",
        "subs",
        "sv",
        "synonym",
        "tax",
        "taxonomy",
        "time",
        "tiobe",
        "tiobeindex",
        "tlist",
        "today",
        "topartist",
        "topmonth",
        "topweek",
        "topyear",
        "tr",
        "tran",
        "translate",
        "triforce",
        "tv",
        "tv_last",
        "tv_next",
        "tv_prev",
        "tw",
        "twatter",
        "twinfo",
        "twitch",
        "twitchtv",
        "twitter",
        "twuser",
        "tz",
        "u",
        "ud",
        "up",
        "upcoming",
        "urban",
        "usage",
        "validate",
        "verse",
        "w",
        "w3c",
        "wa",
        "we",
        "weather",
        "whois",
        "wiki",
        "wikilist",
        "wikipedia",
        "wisdom",
        "wolframalpha",
        "word",
        "wordexample",
        "wordoftheday",
        "wordpass",
        "wordpassword",
        "wordrandom",
        "wordusage",
        "wpass",
        "y",
        "yomama",
        "yomomma",
        "yomommy",
        "yomumma",
        "youtime",
        "youtube",
        "yt",
        "ytime",
        "ytn",
        "sketchfab",
        "sk",
        "skn",
        "skt",
        "zombs",
    }
) | frozenset(
    # Media-generation commands the agent composes into videos: narration audio (.tts returns
    # a WAV url), transcription (.stt), and image generation/editing (gemimg/gemedit/gemt).
    {"tts", "stt", "gemimg", "gemedit", "gemt"}
)


_PR_URL_RE = re.compile(r"https://github\.com/[\w.\-]+/[\w.\-]+/pull/\d+")
_PR_CLAIM_RE = re.compile(
    r"\b(PR (?:opened|created)|pull request (?:opened|created))", re.I
)

# Hosts the bot publishes its own work to; a link to one is a claim it made the
# thing. The paste host is read from the live config, not the default.
_GAMES_HOST = "games.h4ks.com"
_PASTE_HOST = urlsplit(web.pastebins.get("girafiles").url).netloc
_ARTIFACT_HOSTS = (_PASTE_HOST, _GAMES_HOST)
_ARTIFACT_URL_RE = re.compile(
    r"https?://[\w.\-]*(?:%s)\S*"
    % "|".join(re.escape(host) for host in _ARTIFACT_HOSTS)
)
_URL_TRAILERS = ".,;:!?*)]}>" + "\"'`"

_MANIFEST_PREFIX = "[tools used:"
# Only the harness may emit this; the model imitates it from history.
_MANIFEST_RE = re.compile(re.escape(_MANIFEST_PREFIX) + r"[^\]]*\]")


def _artifact_urls(text: str) -> set[str]:
    return {
        url.rstrip(_URL_TRAILERS)
        for url in _ARTIFACT_URL_RE.findall(text or "")
    }


def _artifact_id(url: str) -> str:
    """A games project owns its whole subdomain; a paste id owns its short name
    regardless of the extension the host resolves it under."""
    parts = urlsplit(url)
    if parts.netloc.endswith(_GAMES_HOST):
        return parts.netloc
    stem = parts.path.rsplit("/", 1)[-1]
    return f"{parts.netloc}/{stem.split('.', 1)[0]}"


def _guard_artifact_urls(
    answer: str, produced: set[str], shown: set[str]
) -> str:
    """Remove artifact links the run neither produced nor was shown.

    An invented link still answers HTTP 200 because the host resolves a short id
    ignoring the extension, so liveness proves nothing. A tool upload matches on
    its id, since the model may retype the extension and still mean that file; a
    link merely on screen matches exactly, or relabelling a stale id reads as new
    work.
    """
    made = {_artifact_id(url) for url in produced}
    invented = [
        url
        for url in sorted(_artifact_urls(answer))
        if url not in shown and _artifact_id(url) not in made
    ]
    if not invented:
        return answer
    logger.warning("agent: invented artifact urls: %s", invented)
    for url in invented:
        answer = answer.replace(url, "<nothing-was-uploaded>")
    return "(a link below was invented and removed)\n\n" + answer


def _guard_pr_hallucination(
    answer: str, real_urls: list[str], pr_tool_called: bool
) -> str:
    """Refuse to let the model claim a PR was opened when none actually was.

    The model sometimes invents URLs like 'pull/forkowner:branchname' when
    open_github_pr never returned a valid URL. The guard only fires when the
    tool was actually called but produced no URL — otherwise references to
    /pull/N URLs (chat history, user-supplied PR links, repo navigation,
    str_replace edits to existing PRs) trip a false hallucination warning.
    """
    if real_urls:
        first_url = real_urls[0]
        if first_url in answer:
            return answer
        return f"PR opened: {first_url}\n\n{answer}"
    if not pr_tool_called:
        return answer
    if _PR_CLAIM_RE.search(answer) or _PR_URL_RE.search(answer):
        cleaned = _PR_URL_RE.sub("<no-pr>", answer)
        return (
            "(failed to open PR — open_github_pr returned no valid URL; "
            "the bot's claim below is hallucinated)\n\n" + cleaned
        )
    return answer


def _tool_manifest(tracker) -> str:
    """Compact summary of tools called and their key results for history."""
    if not tracker._results:
        return ""
    parts = []
    for name, snippet in tracker._results:
        snippet = snippet.replace("\n", " ").strip()
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        parts.append(f"{name} → {snippet}")
    return "; ".join(parts)


class _RunTracker(RunHooks):
    """Per-run hook recording tool call sequence + PR URLs + result snippets.

    Names, timing, and short result prefixes only — never full args or
    outputs — so summaries can be pasted publicly without leaking file
    contents, tokens, or API responses.
    """

    def __init__(self):
        self._start = time.monotonic()
        self._calls: list[tuple[str, float]] = []
        self._errors: set[int] = set()
        self._pr_urls: list[str] = []
        self._results: list[tuple[str, str]] = []
        self._artifact_urls: set[str] = set()
        # Populated by _run_agent before the run; when set, tool start/end fan out draft/bot-tools steps.
        self._wf_conn = None
        self._wf_target: str = ""
        self._wf_id: str = ""
        self._step_seq: int = 0
        self._step_ids: dict[int, str] = {}

    def attach_workflow(self, conn, target: str, workflow_id: str) -> None:
        self._wf_conn = conn
        self._wf_target = target
        self._wf_id = workflow_id

    async def on_tool_start(self, context, agent, tool) -> None:
        logger.info("agent: tool invoked: %s", tool.name)
        self._calls.append((tool.name, time.monotonic() - self._start))
        if self._wf_conn and self._wf_id:
            try:
                self._step_seq += 1
                sid = "s%d" % self._step_seq
                self._step_ids[len(self._calls) - 1] = sid
                bot_cmds.emit_step(
                    self._wf_conn,
                    self._wf_target,
                    self._wf_id,
                    sid,
                    "tool-call",
                    "start",
                    tool=tool.name,
                )
            except ValueError:
                logger.debug(
                    "agent: bot-tools step emit skipped (connection closed)"
                )

    async def on_tool_end(self, context, agent, tool, result) -> None:
        result_str = str(result or "")
        prefix = result_str[:20]
        failed = (
            prefix.startswith("(error")
            or prefix.startswith("(mcp")
            or prefix.startswith("(tool error")
        )
        if failed:
            for i in range(len(self._calls) - 1, -1, -1):
                if self._calls[i][0] == tool.name and i not in self._errors:
                    self._errors.add(i)
                    break
        else:
            self._results.append((tool.name, result_str[:120]))
            self._artifact_urls |= _artifact_urls(result_str)
            if tool.name == "open_github_pr":
                m = _PR_URL_RE.search(result_str)
                if m:
                    self._pr_urls.append(m.group(0))
        if self._wf_conn and self._wf_id:
            try:
                idx = len(self._calls) - 1
                paired_sid = self._step_ids.get(idx) or ("s%d" % (idx + 1))
                bot_cmds.emit_step(
                    self._wf_conn,
                    self._wf_target,
                    self._wf_id,
                    paired_sid,
                    "tool-call",
                    "failed" if failed else "complete",
                    tool=tool.name,
                )
                # Snippet only: never put full tool output on the wire.
                snippet = result_str[:200]
                self._step_seq += 1
                bot_cmds.emit_step(
                    self._wf_conn,
                    self._wf_target,
                    self._wf_id,
                    "s%d" % self._step_seq,
                    "tool-result",
                    "failed" if failed else "complete",
                    tool=tool.name,
                    content=snippet,
                )
            except ValueError:
                logger.debug(
                    "agent: bot-tools step emit skipped (connection closed)"
                )

    def failure_summary(self, err: BaseException) -> str:
        err_type = type(err).__name__
        if not self._calls:
            return f"[{err_type}] failed before any tool call"
        parts = [
            f"{n}!" if i in self._errors else n
            for i, (n, _) in enumerate(self._calls)
        ]
        total = len(parts)
        if total > 10:
            parts = [f"…+{total - 9}"] + parts[-9:]
        return f"[{err_type}] {total} tool calls: {'→'.join(parts)}"

    def failure_paste_md(
        self, err: BaseException, prompt: str, backends: list[str]
    ) -> str:
        """Paste-safe markdown report. Tool batches within 0.5s collapsed to one entry."""
        err_type = type(err).__name__
        elapsed = time.monotonic() - self._start
        msg = sanitise_err_message(str(err))[:300]
        lines = [
            "# Agent Failure Report",
            "",
            (
                f"**Error**: `{err_type}: {msg}`"
                if msg
                else f"**Error**: `{err_type}`"
            ),
            f"**Elapsed**: {elapsed:.1f}s",
            f"**Backends tried**: {', '.join(backends)}",
            f"**Tool calls**: {len(self._calls)} ({len(self._errors)} errored)",
            f"**Prompt**: {prompt}",
            "",
            "## Tool Call Sequence",
            "",
        ]
        batches: list[list[tuple[int, str, float]]] = []
        for i, (name, offset) in enumerate(self._calls):
            if batches and offset - batches[-1][0][2] < 0.5:
                batches[-1].append((i, name, offset))
            else:
                batches.append([(i, name, offset)])
        for b_idx, batch in enumerate(batches, 1):
            offset = batch[0][2]
            if len(batch) == 1:
                i, name, _ = batch[0]
                flag = " ❌" if i in self._errors else ""
                lines.append(f"{b_idx}. `{name}`{flag}  *(+{offset:.1f}s)*")
            else:
                lines.append(
                    f"{b_idx}. **parallel batch** ({len(batch)} calls)  *(+{offset:.1f}s)*"
                )
                for i, name, _ in batch:
                    flag = " ❌" if i in self._errors else ""
                    lines.append(f"   - `{name}`{flag}")
        if not self._calls:
            lines.append("*(no tools were called)*")
        return "\n".join(lines)


# Per-bot caches keyed by id(bot). Built lazily — on_start fires per-plugin
# during load, before sibling plugins have registered commands.
_TOOLS_CACHE: dict[int, list[FunctionTool]] = {}
_AGENT_CACHE: dict[int, Agent] = {}

_RECENT_CHAT_LINES = 6
_AGENT_HISTORY_MAX = 20
# Recall injects an index of saved memories (key + short preview) so the agent
# is always aware of what it knows without dumping full values; it pulls detail
# on demand via the memory_get / memory_search tools. Caps bound the prompt.
_MEMORY_INDEX_MAX = 80
_MEMORY_PREVIEW_CHARS = 120
_MEMORY_INDEX_CHAR_BUDGET = 4000

# The bot never receives its own PRIVMSGs, so conn.history (incoming only) can't
# show what the bot itself just said. An irc_out hook records the bot's recent
# outgoing channel lines here (RAM only, per connection+channel) so the agent
# knows what it output when users reference "that"/"the last result".
_OUTPUT_HISTORY_MAX = 20
_OUTPUT_RECALL_LINES = 12
_OUTPUT_PREVIEW_CHARS = 150
_OUTPUT_CHAR_BUDGET = 2000
_CHANNEL_PREFIXES = ("#", "&", "+", "!")

_AGENT_HISTORY: dict[str, deque[dict]] = {}
_BOT_OUTPUTS: dict[tuple[str, str], deque[tuple[float, str]]] = {}


def _get_agent_history(chan: str) -> deque[dict]:
    if chan not in _AGENT_HISTORY:
        _AGENT_HISTORY[chan] = deque(maxlen=_AGENT_HISTORY_MAX)
    return _AGENT_HISTORY[chan]


def _attribute(nick: str, text: str) -> str:
    """Tag a user message with its speaker so multi-user history stays distinct.

    The chat format has a single 'user' role, so without this every speaker in a
    channel collapses into one undifferentiated voice and the model thinks it's
    talking to the same person.
    """
    return f"<{nick}> {text}"


def _history_to_input(
    history: deque[dict], current_prompt: str, nick: str
) -> list[dict]:
    items = list(history)
    items.append({"role": "user", "content": _attribute(nick, current_prompt)})
    return items


def _record_bot_output(conn_name: str, target: str, text: str) -> None:
    key = (conn_name, target)
    buf = _BOT_OUTPUTS.get(key)
    if buf is None:
        buf = deque(maxlen=_OUTPUT_HISTORY_MAX)
        _BOT_OUTPUTS[key] = buf
    buf.append((time.time(), text))


@hook.irc_out()
def capture_bot_output(parsed_line, conn, line):
    """Record the bot's own outgoing channel lines for agent recall.

    An out-sieve MUST return the line unchanged or the message is dropped, so
    the body is fully guarded and always returns ``line``.
    """
    try:
        if parsed_line is not None:
            command = str(parsed_line.command)
            params = parsed_line.parameters
            if command in ("PRIVMSG", "NOTICE") and len(params) >= 2:
                target = str(params[0])
                text = str(params[-1])
                if target.startswith(_CHANNEL_PREFIXES) and text:
                    text = text.replace("\x01ACTION ", "* ").replace("\x01", "")
                    _record_bot_output(conn.name, target, text)
    except (AttributeError, IndexError):
        pass
    return line


def _build_output_recall(event) -> str:
    """Inject the bot's own recent outputs in this channel.

    conn.history holds only incoming messages, so without this the agent has no
    record of what it itself just said when a user references it.
    """
    conn = getattr(event, "conn", None)
    chan = getattr(event, "chan", "") or ""
    conn_name = getattr(conn, "name", "") if conn else ""
    if not chan or not conn_name:
        return ""
    buf = _BOT_OUTPUTS.get((conn_name, chan))
    if not buf:
        return ""
    lines: list[str] = []
    used = 0
    for _ts, text in list(buf)[-_OUTPUT_RECALL_LINES:]:
        if len(text) > _OUTPUT_PREVIEW_CHARS:
            text = text[:_OUTPUT_PREVIEW_CHARS].rstrip() + "…"
        used += len(text)
        if used > _OUTPUT_CHAR_BUDGET:
            break
        lines.append(f"- {text}")
    if not lines:
        return ""
    return (
        "\n## Your Recent Outputs (what you, the bot, last sent to this "
        "channel — reference if a user mentions your previous answers)\n"
        + "\n".join(lines)
    )


class CaptureEvent(CommandEvent):
    """CommandEvent that captures every IRC-bound output method into a list.

    Some plugins use mixed patterns (return string + call reply), so all
    output paths are intercepted here.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._captured: list[str] = []

    def message(self, message, target=None):
        if isinstance(message, (list, tuple)):
            self._captured.extend(str(m) for m in message)
        else:
            self._captured.append(str(message))

    def reply(
        self, *messages, target=None, ping_own_line=False, extra_tags=None
    ):
        for msg in messages:
            if isinstance(msg, (list, tuple)):
                self._captured.extend(str(m) for m in msg)
            else:
                self._captured.append(str(msg))

    def action(self, message, target=None):
        self._captured.append(f"* {message}")

    def notice(self, message, target=None):
        self._captured.append(str(message))

    def notice_doc(self, target=None):
        if self.hook and self.hook.doc:
            self._captured.append(
                f"{self.triggered_prefix}{self.triggered_command} {self.hook.doc}"
            )


def _build_tool(cmd_name: str, cmd_hook) -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper, args_json: str) -> str:
        try:
            data = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError:
            data = {}
        text = str(data.get("text") or "").strip()
        irc_event = ctx.context

        capture = CaptureEvent(
            hook=cmd_hook,
            text=text,
            triggered_command=cmd_name,
            base_event=irc_event,
            cmd_prefix=".",
        )
        # launch() runs the rate_limit/check_disabled/check_acls sieves;
        # internal_launch() would skip them — we want them enforced per call.
        ok = await irc_event.bot.plugin_manager.launch(cmd_hook, capture)
        if not ok:
            return f"(command .{cmd_name} errored)"

        out = (
            "\n".join(capture._captured) if capture._captured else "(no output)"
        )
        return out[:4000]

    description = (cmd_hook.doc or f"Run the .{cmd_name} IRC command.")[:300]
    return FunctionTool(
        name=cmd_name,
        description=description,
        params_json_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Arguments / input text for the command.",
                },
            },
        },
        on_invoke_tool=on_invoke,
    )


def _build_big_brain_tool(bot, cfg: dict):
    """Build a think_hard tool backed by a more capable model as a sub-agent."""
    bb_cfg = cfg.get("big_brain") or {}
    if not bb_cfg.get("enabled"):
        return None

    base_url = bb_cfg.get("base_url", "https://api.z.ai/api/coding/paas/v4")
    model = bb_cfg.get("model") or "glm-5.1"
    api_key_path = bb_cfg.get("api_key_config_path") or "z_ai"
    api_key = bot.config.get_api_key(api_key_path)
    if not api_key:
        logger.warning("agent: big_brain enabled but no api key")
        return None

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    bb_model = OpenAIChatCompletionsModel(model=model, openai_client=client)

    bb_agent = Agent(
        name="BigBrain",
        model=bb_model,
        instructions=(
            "You are a deep reasoning assistant. Analyze the problem thoroughly. "
            "Consider edge cases, provide step-by-step reasoning, and give a clear "
            "conclusion. Be precise and technical. If the problem has multiple valid "
            "approaches, explain the trade-offs and recommend the best one."
        ),
    )
    return bb_agent.as_tool(
        tool_name="think_hard",
        tool_description=(
            "Delegate a complex problem to a more capable reasoning model. "
            "Use for: difficult math, multi-step logic, uncertain code architecture "
            "decisions, debugging complex issues, or when you need a second opinion "
            "on a tricky problem. Do NOT use for simple lookups or straightforward tasks."
        ),
    )


def _get_or_build_tools(bot, cfg: dict) -> list[FunctionTool]:
    key = id(bot)
    if key in _TOOLS_CACHE:
        return _TOOLS_CACHE[key]

    include_cfg = cfg.get("include") or []
    include = set(include_cfg) if include_cfg else set(DEFAULT_INCLUDE)
    exclude = set(cfg.get("exclude") or [])

    tools: list[FunctionTool] = []
    for cmd_hook in bot.plugin_manager.unique_commands():
        names = set(cmd_hook.aliases)
        if names & exclude:
            continue
        if include and names.isdisjoint(include):
            continue
        if cmd_hook.permissions:
            continue
        tools.append(_build_tool(cmd_hook.name, cmd_hook))

    custom = build_custom_tools()
    tools.extend(custom)

    remote = build_mcp_tools(bot)
    tools.extend(remote)

    bb_tool = _build_big_brain_tool(bot, cfg)
    if bb_tool:
        tools.append(bb_tool)
        logger.info("agent: big brain tool enabled")

    logger.info(
        "agent: built %d tools (%d custom, %d mcp)",
        len(tools),
        len(custom),
        len(remote),
    )
    _TOOLS_CACHE[key] = tools
    return tools


def _build_gh_suffix(bot, cfg: dict) -> str:
    gh_user = fetch_github_username(bot)
    can_push = fetch_self_repo_push(bot)
    self_repo = (
        ((bot.config.get("plugins") or {}).get("agent") or {}).get("github_mcp")
        or {}
    ).get("self_repo") or "h4ks-com/CloudBot"
    if can_push:
        return (
            f" GOOD NEWS: your PAT has direct push access to '{self_repo}'. "
            f"Skip fork_github_repo entirely. Call create_github_branch on '{self_repo}' directly, "
            f"then edit_github_file on '{self_repo}', then open_github_pr with head='<branch-name>' "
            f"(not 'user:branch'). Faster and avoids fork-race issues."
        )
    if gh_user:
        return (
            f" Your GitHub username (the PAT owner) is '{gh_user}'. "
            f"You do NOT have direct push access to '{self_repo}', so fork_github_repo first; "
            f"the fork lives at '{gh_user}/repo'. ALL subsequent create_github_branch and "
            f"edit_github_file calls MUST use '{gh_user}/repo' as the repo arg — NOT the parent. "
            f"For open_github_pr, set head='{gh_user}:branch-name' (cross-fork) and base='main', "
            f"target repo is '{self_repo}'."
        )
    return ""


def _build_memory_recall(event) -> str:
    """Inject an index of what the agent has saved that bears on this moment.

    Covers every scope the caller can see — them, this channel, this network —
    because a fact is only worth saving if it comes back on its own when it is
    relevant. Lists each memory's key with a short preview so the agent is aware
    of what it knows without dumping full values; it reads the full value with
    memory_get(key) or finds entries with memory_search(text) when it needs to.
    Newest first, capped by count and characters.
    """
    namespaces = memory_read_namespaces(event)
    if not namespaces:
        return ""
    try:
        memories = all_memories(namespaces, _MEMORY_INDEX_MAX)
    except SQLAlchemyError:
        logger.exception("agent: memory recall failed")
        return ""
    lines: list[str] = []
    used = 0
    for key, value in memories:
        preview = value or ""
        if len(preview) > _MEMORY_PREVIEW_CHARS:
            preview = preview[:_MEMORY_PREVIEW_CHARS].rstrip() + "…"
        line = f"- {key}: {preview}"
        used += len(line)
        if used > _MEMORY_INDEX_CHAR_BUDGET:
            break
        lines.append(line)
    if not lines:
        return ""
    return (
        "\n## Your Memory (saved facts about this user, this channel and this "
        "network — previews shown; call memory_get(key) for a full value, "
        "memory_search(text) to find by content)\n" + "\n".join(lines)
    )


def _build_artifact_recall(event) -> str:
    """Inject the artifacts (videos, songs) recently produced in this channel so
    a follow-up like "make that video longer" can find and iterate on them —
    for a video, video_get_recipe on its URL recovers the exact recipe to tweak.
    """
    chan = getattr(event, "chan", "") or ""
    runs = recent_runs(chan)
    if not runs:
        return ""
    lines = [
        f'- [{record.kind}] "{record.summary}" — {record.url}'
        for record in runs
    ]
    return (
        "\n## Recent Creations (newest first — to modify/improve one, reuse it: "
        "videos via video recipe recovery, songs via cover/recompose)\n"
        + "\n".join(lines)
    )


def _make_dynamic_instructions(base_instructions: str, gh_suffix: str):
    """Return a callable suitable for Agent(instructions=...).

    Called once per Runner.run() — builds per-request system prompt with
    ambient channel context separated from the base instructions.
    """

    def _instructions(ctx, agent):
        event = ctx.context
        snippet = recent_chat_snippet(
            event.conn, event.chan, _RECENT_CHAT_LINES
        )
        ts = datetime.now().strftime("%H:%M:%S")
        parts = [
            base_instructions,
            gh_suffix,
            skill_index(),
            "\n\n## Current Request Context",
            f"- Channel: {event.chan}",
            f"- User asking: {event.nick}",
            f"- Time: {ts}",
        ]
        recall = _build_memory_recall(event)
        if recall:
            parts.append(recall)
        artifacts = _build_artifact_recall(event)
        if artifacts:
            parts.append(artifacts)
        notebooks = notebook_context()
        if notebooks:
            parts.append(notebooks)
        outputs = _build_output_recall(event)
        if outputs:
            parts.append(outputs)
        if snippet:
            parts.append(
                "\n## Recent Channel Messages (background — NOT tasks to act on)\n"
                "These are ambient messages for situational awareness. "
                "Do NOT react to them unless the current task explicitly references them.\n"
                + snippet
            )
        built = "\n".join(parts)
        # Links already on screen are fair for the model to repeat.
        event.agent_context_urls = _artifact_urls(built)
        return built

    return _instructions


def _get_or_build_agent(bot, cfg: dict, tools: list[FunctionTool]) -> Agent:
    key = id(bot)
    if key in _AGENT_CACHE:
        return _AGENT_CACHE[key]
    base_instructions = cfg.get("instructions") or AGENT_INSTRUCTIONS
    gh_suffix = _build_gh_suffix(bot, cfg)
    instructions_fn = _make_dynamic_instructions(base_instructions, gh_suffix)
    # create_video is a fire-and-forget dispatch: it spawns the render and the
    # background job posts progress and the finished video itself. Making it a
    # stop tool ends the turn at the dispatch, so the model never gets a final
    # turn to narrate a video that does not exist yet (and one LLM turn is saved).
    agent = Agent(
        name="CloudBot",
        instructions=instructions_fn,
        tools=tools,
        tool_use_behavior=StopAtTools(
            stop_at_tool_names=["create_video", "create_voice"]
        ),
    )
    _AGENT_CACHE[key] = agent
    return agent


def _make_run_config(cfg: dict, bot, backend: str) -> RunConfig:
    backends = cfg.get("backends") or {}
    if backend not in backends:
        raise ValueError(f"agent backend '{backend}' not configured")
    b = backends[backend]
    api_key = resolve_config_path(bot, b.get("api_key_config_path", ""))
    if not api_key:
        raise ValueError(f"agent backend '{backend}' missing api key")

    base_url = b["base_url"]
    model = b["model"]

    # Ollama uses X-API-Key header, not Authorization: Bearer.
    if b.get("auth_header") == "x-api-key":
        client = AsyncOpenAI(
            base_url=base_url,
            api_key="dummy",
            default_headers={"X-API-Key": api_key},
        )
        return RunConfig(
            model=OpenAIChatCompletionsModel(model=model, openai_client=client)
        )

    return RunConfig(
        model=model,
        model_provider=OpenAIProvider(
            base_url=base_url,
            api_key=api_key,
            use_responses=False,
        ),
    )


def _format_answer(text: str, cfg: dict) -> tuple[list[str], bool]:
    """Return (reply messages, ping_own_line). On overflow the paste link leads
    the first line so it rides the nick ping (``(nick) full: <url>``) with the
    answer below; otherwise a multi-line answer puts the ping on its own line so
    markdown headings/lists render."""
    lines, url = wrap_reply_lines(
        text,
        max_lines=int(cfg.get("reply_max_lines", 10)),
        max_line_bytes=int(cfg.get("reply_max_chars", 420)),
        paste=lambda: upload_markdown_paste(text),
    )
    if url:
        return [f"full: {url}", *lines], False
    return lines, len(lines) > 1


async def _run_agent(event, prompt: str) -> None:
    bot = event.bot
    cfg = bot.config.get("plugins", {}).get("agent", {}) or {}
    if not cfg or not cfg.get("enabled", False) or not cfg.get("backends"):
        logger.info("agent: aborting — disabled or no backends configured")
        return

    enabled_chans = cfg.get("enabled_channels") or []
    if enabled_chans and event.chan not in enabled_chans:
        logger.info(
            "agent: aborting — chan %s not in enabled_channels", event.chan
        )
        return

    prompt = (prompt or "").strip()
    if not prompt:
        event.reply("Agent prompt is empty.")
        return

    tools = _get_or_build_tools(bot, cfg)
    if not tools:
        event.reply(
            "Agent has no tools available (check include/exclude config)."
        )
        return
    agent = _get_or_build_agent(bot, cfg, tools)

    timeout = float(cfg.get("timeout_s", 120))
    max_turns = int(cfg.get("max_turns", 20))
    backends_to_try = [cfg.get("backend", "z_ai")]
    fallback = cfg.get("fallback_backend")
    if fallback and fallback != backends_to_try[0]:
        backends_to_try.append(fallback)

    history = _get_agent_history(event.chan)
    agent_input = _history_to_input(history, prompt, event.nick)

    typing_id = id(event)
    target = event.chan or event.nick
    await start_typing_for_command(event.conn, target, typing_id)
    logger.info(
        "agent: starting LLM call, backends=%s timeout=%s history=%d",
        backends_to_try,
        timeout,
        len(history),
    )
    tracker = _RunTracker()
    event.agent_context_urls = set()

    # Each .agi run is one draft/bot-tools workflow; its steps are the tool calls.
    workflow_id = None
    trigger_msgid = ""
    marked = False
    last_err: BaseException | None = None
    try:
        workflow_id = uuid.uuid4().hex[:12]
        trigger_msgid = event.tag_value("msgid") or ""
        tracker.attach_workflow(event.conn, target, workflow_id)
        bot_cmds.emit_workflow(
            event.conn,
            target,
            workflow_id,
            "start",
            name="agent",
            trigger=trigger_msgid,
            features=["reasoning", "interactive"],
        )
        marked = bool(
            trigger_msgid
            and bot_cmds.mark_workflow(event.conn, trigger_msgid, workflow_id)
        )
    except ValueError:
        logger.debug(
            "agent: bot-tools workflow start skipped (connection closed)"
        )

    backends_tried: list[str] = []
    invoker = event.nick
    cancel_requested = False
    run_cancelled = False
    run_task = asyncio.ensure_future(
        _try_backends(
            agent,
            agent_input,
            event,
            backends_to_try,
            backends_tried,
            tracker,
            cfg,
            timeout,
            max_turns,
            bot,
            history,
            prompt,
        )
    )

    def on_action(action_event, msg):
        nonlocal cancel_requested
        # Only the invoker can cancel their own still-running job.
        if (
            msg.get("action") == "cancel"
            and not run_task.done()
            and action_event.nick.casefold() == invoker.casefold()
        ):
            cancel_requested = True
            run_task.cancel()

    if workflow_id:
        event.conn.memory.setdefault("bot_tools_actions", {})[
            workflow_id
        ] = on_action

    try:
        last_err = await run_task
        if last_err:
            _report_failure(event, tracker, last_err, prompt, backends_tried)
        elif not backends_tried:
            event.reply("Agent failed: no backends tried")
    except asyncio.CancelledError:
        # A cancel that loses the race to natural completion never reaches here,
        # so the run actually stopped: drop the pending so its reply can't stamp
        # a stale "complete" terminal, then close the workflow as cancelled below.
        if not cancel_requested:
            raise
        run_cancelled = True
        if trigger_msgid:
            bot_cmds.discard_pending(event.conn, trigger_msgid)
        event.reply("cancelled")
    finally:
        if workflow_id:
            event.conn.memory.get("bot_tools_actions", {}).pop(
                workflow_id, None
            )
        await stop_typing_for_command(event.conn, target, typing_id)
        # When mark_workflow opted a pending invocation in, the terminal rides the
        # reply tags; otherwise (or on cancel) close it with a standalone TAGMSG.
        if workflow_id and (run_cancelled or not marked):
            try:
                state = (
                    "cancelled"
                    if run_cancelled
                    else "failed" if last_err else "complete"
                )
                bot_cmds.emit_workflow(
                    event.conn,
                    target,
                    workflow_id,
                    state,
                    cancelled_by=invoker if run_cancelled else None,
                )
            except ValueError:
                logger.debug(
                    "agent: bot-tools workflow close skipped (connection closed)"
                )


# Backend failures that fall through to the next backend. Escaping here kills
# the hook and the channel never hears back.
_BACKEND_ERRORS = (
    OpenAIError,
    AgentsException,
    KeyError,
    AttributeError,
    ValueError,
    RuntimeError,
)


def _note_retry(
    event, backend: str, err: BaseException, remaining: list[str]
) -> None:
    """Announce a fallback before it starts; each backend gets the full timeout,
    so a silent retry reads as the bot ignoring you."""
    if remaining:
        event.reply(
            f"{backend} failed ({type(err).__name__}), retrying on {remaining[0]}"
        )


async def _try_backends(
    agent,
    agent_input,
    event,
    backends_to_try,
    backends_tried,
    tracker,
    cfg,
    timeout,
    max_turns,
    bot,
    history,
    prompt,
) -> BaseException | None:
    """Run the agent against each backend in order. Return the last error (or
    None on success — in which case we've already replied to IRC and the caller
    skips the failure path)."""
    last_err: BaseException | None = None
    for index, backend in enumerate(backends_to_try):
        backends_tried.append(backend)
        remaining = backends_to_try[index + 1 :]
        try:
            run_cfg = _make_run_config(cfg, bot, backend)
        except (KeyError, ValueError) as e:
            logger.warning(
                "agent: cannot build run config for %s: %s", backend, e
            )
            last_err = e
            continue
        try:
            result = await asyncio.wait_for(
                Runner.run(
                    agent,
                    agent_input,
                    context=event,
                    run_config=run_cfg,
                    hooks=tracker,
                    max_turns=max_turns,
                ),
                timeout=timeout,
            )
        except BadRequestError as e:
            if "context_length_exceeded" not in str(e) or len(history) == 0:
                logger.warning("agent: %s bad request: %s", backend, e)
                last_err = e
                continue
            dropped = min(4, len(history))
            for _ in range(dropped):
                history.popleft()
            logger.warning(
                "agent: context overflow, dropped %d history items, retrying",
                dropped,
            )
            agent_input = _history_to_input(history, prompt, event.nick)
            try:
                result = await asyncio.wait_for(
                    Runner.run(
                        agent,
                        agent_input,
                        context=event,
                        run_config=run_cfg,
                        hooks=tracker,
                        max_turns=max_turns,
                    ),
                    timeout=timeout,
                )
            except (*_BACKEND_ERRORS, asyncio.TimeoutError) as e2:
                logger.warning(
                    "agent: %s failed after history trim: %s", backend, e2
                )
                last_err = e2
                _note_retry(event, backend, e2, remaining)
                continue
        except asyncio.TimeoutError as e:
            logger.warning("agent: %s timed out after %ss", backend, timeout)
            last_err = e
            _note_retry(event, backend, e, remaining)
            continue
        except MaxTurnsExceeded as e:
            logger.warning("agent: %s hit max turns (%s)", backend, max_turns)
            last_err = e
            break
        except TypeError as e:
            logger.warning(
                "agent: %s returned malformed response: %s", backend, e
            )
            last_err = e
            break
        except _BACKEND_ERRORS as e:
            logger.warning(
                "agent: %s failed: %s: %s", backend, type(e).__name__, e
            )
            last_err = e
            _note_retry(event, backend, e, remaining)
            continue

        answer = str(result.final_output or "").strip() or "(no answer)"
        pr_tool_called = any(
            name == "open_github_pr" for name, _ in tracker._calls
        )
        answer = _guard_pr_hallucination(
            answer, tracker._pr_urls, pr_tool_called
        )
        if _MANIFEST_RE.search(answer):
            logger.warning("agent: model forged a [tools used:] tag")
            answer = _MANIFEST_RE.sub("", answer).strip()
        answer = _guard_artifact_urls(
            answer,
            tracker._artifact_urls,
            event.agent_context_urls
            | _artifact_urls(
                " ".join(
                    str(message.get("content") or "") for message in agent_input
                )
            ),
        )

        manifest = _tool_manifest(tracker)
        history.append(
            {"role": "user", "content": _attribute(event.nick, prompt)}
        )
        if manifest:
            history.append(
                {
                    "role": "assistant",
                    "content": f"{answer}\n{_MANIFEST_PREFIX} {manifest}]",
                }
            )
        else:
            history.append({"role": "assistant", "content": answer})
        reply_lines, ping_own_line = _format_answer(answer, cfg)
        event.reply(*reply_lines, ping_own_line=ping_own_line)
        return None
    return last_err


def _report_failure(event, tracker, err, prompt, backends_tried) -> None:
    summary = tracker.failure_summary(err)
    try:
        md = tracker.failure_paste_md(err, prompt, backends_tried)
        paste_url = upload_markdown_paste(md)
    except (OSError, ValueError, RuntimeError):
        logger.exception("agent: failure paste upload failed")
        event.reply(f"Agent failed: {summary}")
        return
    event.reply(f"Agent failed: {summary} — details: {paste_url}")


@hook.on_start()
def _init_agent_tables(bot):
    """Build the tables backing the agent's own tools.

    They are created here because this plugin is what registers those tools: the
    tool modules are imported unconditionally, so a table owned by any other
    plugin could be missing while its tool is still callable.
    """
    try:
        ensure_fts(bot.db_engine)
    except SQLAlchemyError:
        logger.exception("agent: memory init failed")
    try:
        ensure_kaggle_table(bot.db_engine)
    except SQLAlchemyError:
        logger.exception("agent: kaggle table init failed")
    discover(bot)


@hook.command("agi", "agent", "ask", autohelp=False, allow_private=False)
async def agent_command(text, event):
    """<prompt> - ask the bot in natural language; uses any available tool."""
    if not text:
        event.reply("usage: .agi <natural language prompt>")
        return
    await _run_agent(event, text)


@hook.command("reloadmcp", permissions=["botcontrol"], autohelp=False)
async def reload_mcp(bot) -> str:
    """reloads config and re-polls the agent's MCP servers. Owner only."""
    bot.config.load_config()
    _TOOLS_CACHE.pop(id(bot), None)
    _AGENT_CACHE.pop(id(bot), None)
    servers, tools = await run_in_executor(reload_servers, bot)
    return f"MCP reloaded: {servers} server(s), {tools} tool(s)."
