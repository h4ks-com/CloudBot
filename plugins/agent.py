"""Agentic LLM dispatcher for CloudBot.

Explicit-only entry: .ask / .agent / .agi <prompt>. Routes to an LLM agent
that can call existing bot commands as tools. Uses openai-agents 0.0.6
with Z.AI glm-5 (primary) and OpenRouter (fallback).

Tool definitions live in cloudbot.agent (outside plugins/) so the plugin
manager doesn't try to load them as plugins.
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

from agents import Agent, FunctionTool, RunContextWrapper, RunHooks, Runner
from agents.exceptions import MaxTurnsExceeded
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_provider import OpenAIProvider
from agents.run import RunConfig
from openai import AsyncOpenAI

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
from cloudbot.event import CommandEvent
from cloudbot.util import formatting
from cloudbot.util.typing import (
    start_typing_for_command,
    stop_typing_for_command,
)

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
        "randomword",
        "reddit",
        "rhyme",
        "rhymerel",
        "rinfo",
        "rmods",
        "rottentomatoes",
        "rss",
        "rt",
        "ruser",
        "shorten",
        "sid",
        "slickdeals",
        "so",
        "son",
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
        "zombs",
    }
)


_PR_URL_RE = re.compile(r"https://github\.com/[\w.\-]+/[\w.\-]+/pull/\d+")
_PR_CLAIM_RE = re.compile(
    r"\b(PR (?:opened|created)|pull request (?:opened|created))", re.I
)


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


class _RunTracker(RunHooks):
    """Per-run hook recording tool call sequence + PR URLs.

    Names and timing only — never args or outputs — so summaries can be
    pasted publicly without leaking file contents, tokens, or API responses.
    """

    def __init__(self):
        self._start = time.monotonic()
        self._calls: list[tuple[str, float]] = []
        self._errors: set[int] = set()
        self._pr_urls: list[str] = []

    async def on_tool_start(self, context, agent, tool) -> None:
        logger.info("agent: tool invoked: %s", tool.name)
        self._calls.append((tool.name, time.monotonic() - self._start))

    async def on_tool_end(self, context, agent, tool, result) -> None:
        result_str = str(result or "")
        prefix = result_str[:20]
        if (
            prefix.startswith("(error")
            or prefix.startswith("(mcp")
            or prefix.startswith("(tool error")
        ):
            for i in range(len(self._calls) - 1, -1, -1):
                if self._calls[i][0] == tool.name and i not in self._errors:
                    self._errors.add(i)
                    break
        elif tool.name == "open_github_pr":
            m = _PR_URL_RE.search(result_str)
            if m:
                self._pr_urls.append(m.group(0))

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

# Recent-chat snippet size auto-prepended to each .agi prompt. Gives the model
# light channel context for trivial follow-ups without burning a chat_history
# tool call. We deliberately do NOT carry openai-agents SDK history across
# runs: that history contains prior tool calls and tool outputs, which biased
# the model to "continue" the previous task even when a different user asked
# something unrelated. Each .agi invocation is now a fresh agent run; deeper
# context recall is available via the chat_history / search_history tools.
_RECENT_CHAT_LINES = 6


def _build_recent_chat_snippet(event, n: int) -> str:
    """Format the last n IRC messages as a plain-text reference block.

    Returned with a header that primes the model to treat the snippet as
    background context, not as an open task to continue.
    """
    try:
        history = list(event.conn.history[event.chan])
    except (KeyError, AttributeError):
        return ""
    if not history:
        return ""
    recent = history[-n:]
    lines = []
    for nick, _ts, msg in recent:
        msg = msg.replace("\x01ACTION ", "* ").replace("\x01", "")
        lines.append(f"<{nick}> {msg}")
    body = "\n".join(lines)
    return (
        "[recent channel context — reference only, NOT a task to continue]\n"
        f"{body}\n[end recent context]\n"
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
        if name in seen or name in exclude:
            continue
        if include and name not in include:
            continue
        if cmd_hook.permissions:
            continue
        seen.add(name)
        tools.append(_build_tool(name, cmd_hook))

    custom = build_custom_tools()
    tools.extend(custom)
    logger.info("agent: built %d tools (%d custom)", len(tools), len(custom))
    _TOOLS_CACHE[key] = tools
    return tools


def _get_or_build_agent(bot, cfg: dict, tools: list[FunctionTool]) -> Agent:
    key = id(bot)
    if key in _AGENT_CACHE:
        return _AGENT_CACHE[key]
    instructions = cfg.get("instructions") or AGENT_INSTRUCTIONS
    gh_user = fetch_github_username(bot)
    can_push = fetch_self_repo_push(bot)
    self_repo = (
        ((bot.config.get("plugins") or {}).get("agent") or {}).get("github_mcp")
        or {}
    ).get("self_repo") or "h4ks-com/CloudBot"
    if can_push:
        instructions = instructions + (
            f" GOOD NEWS: your PAT has direct push access to '{self_repo}'. "
            f"Skip fork_github_repo entirely. Call create_github_branch on '{self_repo}' directly, "
            f"then edit_github_file on '{self_repo}', then open_github_pr with head='<branch-name>' "
            f"(not 'user:branch'). Faster and avoids fork-race issues."
        )
    elif gh_user:
        instructions = instructions + (
            f" Your GitHub username (the PAT owner) is '{gh_user}'. "
            f"You do NOT have direct push access to '{self_repo}', so fork_github_repo first; "
            f"the fork lives at '{gh_user}/repo'. ALL subsequent create_github_branch and "
            f"edit_github_file calls MUST use '{gh_user}/repo' as the repo arg — NOT the parent. "
            f"For open_github_pr, set head='{gh_user}:branch-name' (cross-fork) and base='main', "
            f"target repo is '{self_repo}'."
        )
    agent = Agent(name="CloudBot", instructions=instructions, tools=tools)
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


def _format_answer(text: str, cfg: dict) -> list[str]:
    """One IRC line, paste only if it doesn't fit reply_max_chars."""
    max_chars = int(cfg.get("reply_max_chars", 420))
    text = text.strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    collapsed = " - ".join(lines) if lines else text

    if len(collapsed) <= max_chars:
        return [collapsed]

    url = None
    try:
        url = upload_markdown_paste(text)
    except (OSError, ValueError, RuntimeError):
        logger.exception("agent: paste upload failed")

    if url:
        suffix = f" (full: {url})"
        truncated = formatting.truncate(collapsed, max_chars - len(suffix))
        return [truncated + suffix]
    return [formatting.truncate(collapsed, max_chars)]


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
    max_turns = int(cfg.get("max_turns", 8))
    backends_to_try = [cfg.get("backend", "z_ai")]
    fallback = cfg.get("fallback_backend")
    if fallback and fallback != backends_to_try[0]:
        backends_to_try.append(fallback)

    ts = datetime.now().strftime("%H:%M:%S")
    recent_snippet = _build_recent_chat_snippet(event, _RECENT_CHAT_LINES)
    enriched = (
        f"{recent_snippet}"
        f"[channel: {event.chan} | user: {event.nick} | time: {ts}]\n"
        f"This is a NEW task from {event.nick}. Act on the message below — "
        f"do not continue any prior work shown in the recent context above.\n"
        f"{prompt}"
    )
    agent_input = enriched

    typing_id = id(event)
    target = event.chan or event.nick
    await start_typing_for_command(event.conn, target, typing_id)
    logger.info(
        "agent: starting LLM call, backends=%s timeout=%s recent_lines=%d",
        backends_to_try,
        timeout,
        _RECENT_CHAT_LINES,
    )
    tracker = _RunTracker()
    backends_tried: list[str] = []
    try:
        last_err: Optional[BaseException] = await _try_backends(
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
        )
        if last_err:
            _report_failure(event, tracker, last_err, prompt, backends_tried)
        elif not backends_tried:
            event.reply("Agent failed: no backends tried")
    finally:
        await stop_typing_for_command(event.conn, target, typing_id)


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
) -> Optional[BaseException]:
    """Run the agent against each backend in order. Return the last error (or
    None on success — in which case we've already replied to IRC and the caller
    skips the failure path)."""
    last_err: Optional[BaseException] = None
    for backend in backends_to_try:
        backends_tried.append(backend)
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
        except asyncio.TimeoutError as e:
            logger.warning("agent: %s timed out after %ss", backend, timeout)
            last_err = e
            continue
        except MaxTurnsExceeded as e:
            logger.warning("agent: %s hit max turns (%s)", backend, max_turns)
            last_err = e
            break
        except TypeError as e:
            # Provider returned a malformed response (e.g. choices=null) under
            # heavy context. Fallback would replay the same conversation and
            # likely fail the same way.
            logger.warning(
                "agent: %s returned malformed response: %s", backend, e
            )
            last_err = e
            break
        except (KeyError, AttributeError, ValueError, RuntimeError) as e:
            logger.warning(
                "agent: %s failed: %s: %s", backend, type(e).__name__, e
            )
            last_err = e
            continue

        answer = str(result.final_output or "").strip() or "(no answer)"
        pr_tool_called = any(
            name == "open_github_pr" for name, _ in tracker._calls
        )
        answer = _guard_pr_hallucination(
            answer, tracker._pr_urls, pr_tool_called
        )
        event.reply(*_format_answer(answer, cfg))
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


@hook.command("ask", "agent", "agi", autohelp=False)
async def agent_command(text, event):
    """<prompt> - ask the bot in natural language; uses any available tool."""
    if not text:
        event.reply("usage: .ask <natural language prompt>")
        return
    # Public channels only — IRC channels start with #/&/+/!. Private queries
    # have event.chan equal to the user's nick.
    chan = getattr(event, "chan", "") or ""
    if not chan.startswith(("#", "&", "+", "!")) or chan == event.nick:
        event.reply(
            "Agent only available in public channels, not private messages."
        )
        return
    await _run_agent(event, text)
