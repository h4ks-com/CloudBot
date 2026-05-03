"""Agentic LLM dispatcher for CloudBot.

Explicit-only entry: .ask / .agent / .agi <prompt>. Routes to an LLM agent
that can call existing bot commands as tools. Uses openai-agents 0.0.6
with Z.AI glm-5 (primary) and OpenRouter (fallback).
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Optional

import requests
from agents import Agent, FunctionTool, RunContextWrapper, RunHooks, Runner
from agents.exceptions import MaxTurnsExceeded
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_provider import OpenAIProvider
from agents.run import RunConfig
from openai import AsyncOpenAI

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.util import formatting
from cloudbot.util.typing import start_typing_for_command, stop_typing_for_command
from plugins.agent_tools import CUSTOM_TOOLS, upload_markdown_paste

logger = logging.getLogger("cloudbot")

# Aliases of commands curated as safe/useful for LLM tool use.
# Derived from tmp/test_categorize_tools.py verdicts (plugins marked "include").
# Always filtered against: privileged permissions, config `exclude` list.
DEFAULT_INCLUDE = frozenset({
    "1337", "adj", "adjrel", "amazon", "antonym", "api",
    "artist", "arxiv", "arxiv_next", "ast", "astronomy", "aw",
    "awesome", "awn", "ax", "axn", "az", "b", "band", "batteryinfo", "batterystats",
    "bible", "bing", "bingimage", "bis", "bookpun", "books",
    "ca", "cakeday", "calc", "cipher", "compare", "confucious",
    "convert", "cool", "crypto", "cryptocurrency", "currencies",
    "currencylist", "cypher", "dadjoke", "ddg", "ddg_next", "decipher",
    "decypher", "define", "devdocs", "dictionary", "dig", "directions",
    "doc", "docs", "documentation", "dogpile", "doit", "domain",
    "domainr", "down", "dp", "dpis", "drink", "e",
    "etree", "etymology", "expand", "fact", "fakenews", "fc",
    "fcd", "fcw", "feed", "forecast", "forecastweek", "fortune",
    "funtranslate", "g", "gbooks", "gd", "geoguess", "geoip",
    "getlyrics", "gh", "ghissue", "ghn", "ghnext", "ghpaste", "ghsource", "gis",
    "github", "gitio", "gmd", "gn", "googl", "google_translate",
    "gw", "gwn",
    "hltb", "hltb_next", "hltbn", "horoscope", "howlongtobeat", "imdb",
    "imdb_next", "imdbn", "iscool", "isgd", "iss", "issafe",
    "issue", "isup", "karma", "kernel", "kero", "kerowhack",
    "l", "l33t", "la", "langlist", "last", "lastfm",
    "lastfmcompare", "lawyerjoke", "lc", "leet", "leetify", "libreband",
    "librecompare", "librefm", "librela", "librelast", "librelc", "librelibrelta",
    "libreltm", "libreltw", "librenp", "libreplays", "libreta", "libretop",
    "libretopall", "libretopartists", "libretopmonth", "libretoptrack",
    "libretoptracks", "libretopweek", "librett", "locate", "lta", "ltm",
    "ltop", "ltt", "ltw", "lty", "lyn", "lyrics",
    "lyricsn", "lysearch", "maps", "mars", "marslocations", "marslocs",
    "marstime", "math", "mc", "mcp", "mcping", "mcstatus",
    "mcwiki", "mean", "meh", "meta", "metacritic", "metan",
    "moderates", "mods", "moremod", "morse", "morsecode", "morsetrans",
    "news", "newsn", "noun", "np", "octo", "octopart",
    "offline", "passage", "password", "pi", "pig", "piglatin",
    "pkg", "pkglist", "pkgn", "plaudio", "play", "playn", "plays", "plimage",
    "playstation", "playstore", "playstoren", "pronounce", "psn", "psnn",
    "pun", "quran", "randomword", "reddit", "rhyme", "rhymerel",
    "rinfo", "rmods", "rottentomatoes", "rss", "rt", "ruser",
    "shorten", "sid", "slickdeals", "so", "son", "sounditout",
    "soundlike", "spalbum", "spartist", "spell", "spotify", "sptrack",
    "stackoverflow", "steam", "steamcalc", "steamdb", "steamid", "steamuser",
    "stock", "streetview", "su", "sub", "subinfo", "submods",
    "subreddit", "subs", "sv", "synonym", "tax", "taxonomy",
    "time", "tiobe", "tiobeindex", "tlist", "today", "topartist",
    "topmonth", "topweek", "topyear", "tr", "tran", "translate",
    "triforce", "tv", "tv_last", "tv_next", "tv_prev", "tw",
    "twatter", "twinfo", "twitch", "twitchtv", "twitter", "twuser",
    "tz", "u", "ud", "up", "urban", "usage",
    "validate", "verse", "w", "w3c", "wa", "we",
    "weather", "whois", "wiki", "wikilist", "wikipedia", "wisdom",
    "wolframalpha", "word", "wordexample", "wordoftheday", "wordpass", "wordpassword",
    "wordrandom", "wordusage", "wpass", "y", "yomama", "yomomma",
    "yomommy", "yomumma", "youtime", "youtube", "yt", "ytime",
    "ytn", "zombs",
})

AGENT_INSTRUCTIONS = (
    "You are CloudBot, an IRC bot assistant. Users address you in natural language in an IRC channel. "
    "Each user message starts with a context prefix: [channel: #chan | user: nick | time: HH:MM:SS]. "
    "Use the available tools (existing bot commands) to answer. "
    "Call multiple tools in parallel when independent. "
    "Use the chat_history tool when the request refers to something said earlier or needs channel context. "
    "When a message contains an image URL (jpg/png/gif/webp/imgur/i.redd.it etc.), use describe_image to see it. "
    "Keep your final answer concise — IRC lines are short. "
    "When a tool returns raw text, extract and summarise the key information. "
    "YOUR OWN SOURCE CODE lives at GitHub repo 'h4ks-com/CloudBot'. "
    "The ghsource tool maps any bot command name to its exact source file and line number. "
    "WHENEVER a user asks about a bot command (e.g. 'how does .wiki work', 'what model does .agi use', "
    "'show me .we source') — ALWAYS call ghsource <command> FIRST as the very first tool call. "
    "ghsource returns a sentence like: \"Command 'X' is defined in: https://github.com/h4ks-com/CloudBot/blob/main/plugins/foo.py#L42\". "
    "Parse that URL: the file path is everything after '/main/' and before '#L' (e.g. 'plugins/foo.py'); the line number is the integer after '#L' (e.g. 42). "
    "Then call read_github_file with repo='h4ks-com/CloudBot', path='plugins/foo.py', branch='main', start_line=42. "
    "The start_line param extracts code around that line — always pass it when you have a line number, so you don't get a truncated top-of-file snippet. "
    "If ghsource returns 'not found', immediately call list_bot_commands (no args) to get all command names, "
    "find the closest match to what the user typed, then call ghsource again with the correct name. "
    "Do NOT call web_fetch, search_history, search_github_code, or any other tool when a command is not found. "
    "Never use search_github_code as a substitute for ghsource when a command name is known or guessable. "
    "The GitHub tools (list_repo_files, read_github_file, search_github_code, fork_github_repo, "
    "create_github_branch, edit_github_file, open_github_pr) are ALREADY CONFIGURED and authenticated — "
    "call them directly without speculating about credentials or setup. If a tool fails, report the error. "
    "When asked to fix, improve, or add a bot command or plugin: "
    "1) Call ghsource <command> first, extract file path and line number from the URL, then call read_github_file with start_line set to that line number. "
    "2) Use fork_github_repo if needed (wait ~10s after forking). "
    "3) Use create_github_branch with a name like 'fix/command-name'. "
    "4) Use edit_github_file with the COMPLETE new file content (not a diff — full file). "
    "   edit_github_file auto-fetches the file's blob SHA when overwriting an existing file, "
    "   so you do NOT need to pass sha. If a previous edit_github_file call returned an error "
    "   about SHA mismatch, simply call it again — the tool will re-fetch the latest SHA. "
    "5) Use open_github_pr and report the PR URL to the channel. "
    "Always read the full file before editing. You cannot run or test code, so reason carefully about correctness. "
    "EFFICIENCY RULES (you have a hard turn limit — exceed it and the run dies with no PR): "
    "(a) Prefer ONE big file over many small files. If asked to add 30 items, put them in ONE data file or ONE list, "
    "    not 30 separate files. Each edit_github_file costs ~30s sequential + 2 API calls. "
    "    If the user explicitly asks for separated files, cap at 6 total files MAX. "
    "(b) Call fork_github_repo AT MOST ONCE per task. If you forked already, the fork persists — reuse it. "
    "(c) Call create_github_branch AT MOST ONCE per task. If it returned 'already exists' or '(error:', the "
    "    branch is fine — proceed to edits, do not retry. "
    "(d) After fork+branch, do all edits SEQUENTIALLY (never parallel — parallel commits on the "
    "    same branch race and stomp each other), then immediately open_github_pr. "
    "(e) If you've already made >5 edit_github_file calls and the PR isn't open, STOP editing and open the PR with what you have. "
    "(f) Do not re-read files you just wrote. The MCP did the commit — trust the success response. "
    "    BUT: before creating any 'new' file, you MUST read_github_file at that path first. "
    "    If it returns existing substantive content (more than a few lines), pick a different name — "
    "    DO NOT silently overwrite an existing plugin. "
    "(g) HARD CAP: 3 read_github_file + 2 list_repo_files calls TOTAL before starting any edits. "
    "    Do NOT read the same file twice. Do NOT list the same dir twice. "
    "    Each tool result eats context — at ~30 reads you hit the model's input limit and the run dies. "
    "(h) If a tool result starts with '(error:' do NOT retry the same call with the same args — change approach or stop. "
    "(i) Always finish with open_github_pr and report the URL — a task without a PR is a failure. "
    "(j) web_fetch on a URL once. If it fails, move on — do NOT retry the same URL with variations. "
    "(k) Do NOT call paste_markdown to write a 'plan' or 'summary' before doing the work. The user "
    "    wants the PR, not a plan paste. The only paste should be your final reply if too long. "
    "(l) Do NOT use browser_screenshot/browser_console/browser_evaluate for code research — those are "
    "    for live web app testing, not for reading source. Use read_github_file instead."
)

_SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+", re.I),
    re.compile(r"\bgithubmcp_[A-Za-z0-9]+", re.I),
    re.compile(r"\bgh[ps]_[A-Za-z0-9]{20,}", re.I),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bapikey[\"':=\s]+[^\s\"',]+", re.I),
]


def _sanitise_err_message(msg: str) -> str:
    """Strip credentials and tokens from an exception message before pasting."""
    for pat in _SECRET_PATTERNS:
        msg = pat.sub("<redacted>", msg)
    return msg


class _RunTracker(RunHooks):
    """Per-run hook that tracks tool call sequence for safe failure summaries.

    Only records tool names and timing (never args or outputs) so summaries
    can be shown in IRC or pasted without leaking file contents, tokens, or
    API responses.
    """

    def __init__(self):
        self._start = time.monotonic()
        self._calls: list[tuple[str, float]] = []  # (name, offset_from_start)
        self._errors: set[int] = set()

    async def on_tool_start(self, context, agent, tool) -> None:
        logger.info("agent: tool invoked: %s", tool.name)
        self._calls.append((tool.name, time.monotonic() - self._start))

    async def on_tool_end(self, context, agent, tool, result) -> None:
        # Only inspect the very start of result to detect errors — never log full content.
        prefix = str(result or "")[:20]
        if prefix.startswith("(error") or prefix.startswith("(mcp"):
            # Find the latest unresolved call for this tool name (handles parallel calls).
            for i in range(len(self._calls) - 1, -1, -1):
                if self._calls[i][0] == tool.name and i not in self._errors:
                    self._errors.add(i)
                    break

    def failure_summary(self, err: BaseException) -> str:
        err_type = type(err).__name__
        if not self._calls:
            return f"[{err_type}] failed before any tool call"
        parts = [f"{n}!" if i in self._errors else n for i, (n, _) in enumerate(self._calls)]
        total = len(parts)
        # Keep last 10 steps to stay within IRC line length.
        if total > 10:
            parts = [f"…+{total - 9}"] + parts[-9:]
        return f"[{err_type}] {total} tool calls: {'→'.join(parts)}"

    def failure_paste_md(self, err: BaseException, prompt: str, backends: list[str]) -> str:
        """Build a paste-safe markdown failure report.

        Contains: prompt (already public in IRC), tool names, timing,
        error class+sanitised message, backends tried. The message is
        scrubbed of bearer tokens / api keys / Kong key strings before
        inclusion so paste links are safe to share.

        Tools called within the same 0.5s window are grouped as one
        parallel batch so the timeline isn't dominated by `gather()` bursts.
        """
        err_type = type(err).__name__
        elapsed = time.monotonic() - self._start
        msg = _sanitise_err_message(str(err))[:300]
        lines = [
            "# Agent Failure Report",
            "",
            f"**Error**: `{err_type}: {msg}`" if msg else f"**Error**: `{err_type}`",
            f"**Elapsed**: {elapsed:.1f}s",
            f"**Backends tried**: {', '.join(backends)}",
            f"**Tool calls**: {len(self._calls)} ({len(self._errors)} errored)",
            f"**Prompt**: {prompt}",
            "",
            "## Tool Call Sequence",
            "",
        ]
        # Group calls into parallel batches by start offset (within 0.5s).
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
                lines.append(f"{b_idx}. **parallel batch** ({len(batch)} calls)  *(+{offset:.1f}s)*")
                for i, name, _ in batch:
                    flag = " ❌" if i in self._errors else ""
                    lines.append(f"   - `{name}`{flag}")
        if not self._calls:
            lines.append("*(no tools were called)*")
        return "\n".join(lines)

# Per-bot caches keyed by id(bot). Built lazily because on_start fires
# per-plugin during load, before sibling plugins have registered commands.
_TOOLS_CACHE: dict[int, list[FunctionTool]] = {}
_AGENT_CACHE: dict[int, Agent] = {}

# Per-channel conversation history: (conn_name, chan) -> to_input_list() from last run.
# Lets the model remember its own previous outputs across IRC messages.
_CONV_CACHE: dict[tuple[str, str], list] = {}
_CONV_MAX_ITEMS = 12  # keep last ~3 turns (user+assistant+tools each)


class CaptureEvent(CommandEvent):
    """CommandEvent that captures all IRC-bound output in a list.

    Overrides every method on Event/CommandEvent that would send data to
    IRC (message, reply, action, notice, notice_doc). Some plugins use
    mixed patterns (return string + call reply) so both paths are caught.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._captured: list[str] = []

    def message(self, message, target=None):
        if isinstance(message, (list, tuple)):
            self._captured.extend(str(m) for m in message)
        else:
            self._captured.append(str(message))

    def reply(self, *messages, target=None):
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


def _resolve_config_path(bot, dotted: str) -> Optional[str]:
    """Resolve a dotted config path like 'z_ai' or 'plugins.ollama.api_key'.

    Short paths (no dot) look up `api_keys.<name>` via `get_api_key`.
    """
    if "." not in dotted:
        return bot.config.get_api_key(dotted)
    node: Any = bot.config
    for part in dotted.split("."):
        if hasattr(node, "get"):
            node = node.get(part)
        else:
            return None
        if node is None:
            return None
    return node if isinstance(node, str) else None


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
        # launch() runs sieves (rate_limit, check_disabled, check_acls).
        # internal_launch() would skip them — we want them enforced per tool call.
        ok = await irc_event.bot.plugin_manager.launch(cmd_hook, capture)
        if not ok:
            return f"(command .{cmd_name} errored)"

        out = "\n".join(capture._captured) if capture._captured else "(no output)"
        # Cap per-tool output to avoid context blowout when a tool dumps a lot.
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


def _get_or_build_tools(bot, cfg: dict) -> list[FunctionTool]:
    key = id(bot)
    if key in _TOOLS_CACHE:
        return _TOOLS_CACHE[key]

    include_cfg = cfg.get("include") or []
    include = set(include_cfg) if include_cfg else set(DEFAULT_INCLUDE)
    exclude = set(cfg.get("exclude") or [])

    tools: list[FunctionTool] = []
    seen: set[str] = set()
    for name, cmd_hook in bot.plugin_manager.commands.items():
        if name in seen:
            continue
        if name in exclude:
            continue
        if include and name not in include:
            continue
        if cmd_hook.permissions:
            continue
        seen.add(name)
        tools.append(_build_tool(name, cmd_hook))

    tools.extend(CUSTOM_TOOLS)
    logger.info("agent: built %d tools (%d custom)", len(tools), len(CUSTOM_TOOLS))
    _TOOLS_CACHE[key] = tools
    return tools


def _fetch_github_username(bot) -> Optional[str]:
    """Look up the GitHub PAT's owning username so the agent knows its fork prefix."""
    token = bot.config.get_api_key("github")
    if not token:
        return None
    try:
        r = requests.get("https://api.github.com/user",
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/vnd.github+json"},
                         timeout=5)
        r.raise_for_status()
        return r.json().get("login")
    except (requests.RequestException, ValueError, KeyError):
        logger.warning("agent: github /user lookup failed", exc_info=True)
        return None


def _get_or_build_agent(bot, cfg: dict, tools: list[FunctionTool]) -> Agent:
    key = id(bot)
    if key in _AGENT_CACHE:
        return _AGENT_CACHE[key]
    instructions = cfg.get("instructions") or AGENT_INSTRUCTIONS
    gh_user = _fetch_github_username(bot)
    if gh_user:
        instructions = instructions + (
            f" Your GitHub username (the PAT owner) is '{gh_user}'. "
            f"When you fork_github_repo on owner/repo, the fork lives at '{gh_user}/repo'. "
            f"ALL subsequent create_github_branch and edit_github_file calls MUST use '{gh_user}/repo' "
            f"as the repo arg — NOT the parent owner. Calling them on the parent will 404 because you "
            f"don't have write access there."
        )
    agent = Agent(name="CloudBot", instructions=instructions, tools=tools)
    _AGENT_CACHE[key] = agent
    return agent


def _make_run_config(cfg: dict, bot, backend: str) -> RunConfig:
    backends = cfg.get("backends") or {}
    if backend not in backends:
        raise ValueError(f"agent backend '{backend}' not configured")
    b = backends[backend]
    api_key = _resolve_config_path(bot, b.get("api_key_config_path", ""))
    if not api_key:
        raise ValueError(f"agent backend '{backend}' missing api key")

    base_url = b["base_url"]
    model = b["model"]

    # Ollama endpoint uses X-API-Key header, not Authorization: Bearer.
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


def _format_answer(text: str, cfg: dict) -> list[str]:
    """Collapse multiline answer into one IRC line. Paste only if it doesn't fit."""
    max_chars = int(cfg.get("reply_max_chars", 420))
    text = text.strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    collapsed = " - ".join(lines) if lines else text

    if len(collapsed) <= max_chars:
        return [collapsed]

    try:
        url = upload_markdown_paste(text)
    except Exception:
        logger.exception("agent: paste upload failed")
        url = None

    if url:
        suffix = f" (full: {url})"
        truncated = formatting.truncate(collapsed, max_chars - len(suffix))
        return [truncated + suffix]

    return [formatting.truncate(collapsed, max_chars)]


async def _run_agent(event, prompt: str) -> None:
    bot = event.bot
    cfg = bot.config.get("plugins", {}).get("agent", {}) or {}
    logger.info("agent: _run_agent called, prompt=%r, cfg_keys=%s", prompt, list(cfg.keys()) if cfg else "EMPTY")
    if not cfg or not cfg.get("enabled", False) or not cfg.get("backends"):
        logger.info("agent: aborting — enabled=%s backends=%s", cfg.get("enabled") if cfg else "N/A", bool(cfg.get("backends")) if cfg else "N/A")
        return

    enabled_chans = cfg.get("enabled_channels") or []
    if enabled_chans and event.chan not in enabled_chans:
        logger.info("agent: aborting — chan %s not in enabled_channels %s", event.chan, enabled_chans)
        return

    prompt = (prompt or "").strip()
    if not prompt:
        event.reply("Agent prompt is empty.")
        return

    tools = _get_or_build_tools(bot, cfg)
    logger.info("agent: %d tools available", len(tools))
    if not tools:
        event.reply("Agent has no tools available (check include/exclude config).")
        return
    agent = _get_or_build_agent(bot, cfg, tools)

    timeout = float(cfg.get("timeout_s", 120))
    max_turns = int(cfg.get("max_turns", 8))
    backends_to_try = [cfg.get("backend", "z_ai")]
    fallback = cfg.get("fallback_backend")
    if fallback and fallback != backends_to_try[0]:
        backends_to_try.append(fallback)

    # Enrich prompt with IRC context so model knows where it is.
    ts = datetime.now().strftime("%H:%M:%S")
    enriched = f"[channel: {event.chan} | user: {event.nick} | time: {ts}]\n{prompt}"

    # Build input: previous conversation history + new user message.
    conv_key = (event.conn.name, event.chan)
    prev_history = _CONV_CACHE.get(conv_key, [])
    agent_input = prev_history + [{"role": "user", "content": enriched}] if prev_history else enriched

    typing_id = id(event)
    target = event.chan or event.nick
    await start_typing_for_command(event.conn, target, typing_id)
    logger.info("agent: starting LLM call, backends=%s timeout=%s history_items=%d", backends_to_try, timeout, len(prev_history))
    # Fresh tracker per run — never shared across concurrent calls.
    tracker = _RunTracker()
    backends_tried: list[str] = []
    try:
        last_err: Optional[BaseException] = None
        for backend in backends_to_try:
            backends_tried.append(backend)
            try:
                run_cfg = _make_run_config(cfg, bot, backend)
            except Exception as e:
                logger.warning("agent: cannot build run config for %s: %s", backend, e)
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
            except asyncio.TimeoutError as e:
                logger.warning("agent: %s timed out after %ss", backend, timeout)
                last_err = e
                continue
            except MaxTurnsExceeded as e:
                # Model exhausted turns — retrying on a fallback backend repeats the same loop.
                logger.warning("agent: %s hit max turns (%s)", backend, max_turns)
                last_err = e
                break
            except TypeError as e:
                # Provider returned a malformed response (e.g. choices=null) under heavy
                # context. Fallback would replay the same conversation and likely fail
                # the same way — bail out and report the actual cause instead.
                logger.warning("agent: %s returned malformed response: %s", backend, e)
                last_err = e
                break
            except Exception as e:
                # Log full error server-side; never expose str(e) to IRC (may contain tokens/URLs).
                logger.warning("agent: %s failed: %s: %s", backend, type(e).__name__, e)
                last_err = e
                continue

            # Persist conversation history for next invocation, capped to avoid blowout.
            try:
                new_history = result.to_input_list()
                _CONV_CACHE[conv_key] = new_history[-_CONV_MAX_ITEMS:]
            except AttributeError:
                logger.debug("agent: result.to_input_list() unavailable in this SDK version")

            answer = str(result.final_output or "").strip() or "(no answer)"
            event.reply(*_format_answer(answer, cfg))
            return

        if last_err:
            summary = tracker.failure_summary(last_err)
            try:
                md = tracker.failure_paste_md(last_err, prompt, backends_tried)
                paste_url = upload_markdown_paste(md)
                event.reply(f"Agent failed: {summary} — details: {paste_url}")
            except Exception:
                event.reply(f"Agent failed: {summary}")
        else:
            event.reply("Agent failed: no backends tried")
    finally:
        await stop_typing_for_command(event.conn, target, typing_id)


@hook.command("ask", "agent", "agi", autohelp=False)
async def agent_command(text, event):
    """<prompt> - ask the bot in natural language; uses any available tool."""
    if not text:
        event.reply("usage: .ask <natural language prompt>")
        return
    await _run_agent(event, text)
