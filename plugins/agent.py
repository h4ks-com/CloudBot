"""Agentic LLM dispatcher for CloudBot.

Explicit-only entry: .ask / .agent / .agi <prompt>. Routes to an LLM agent
that can call existing bot commands as tools. Uses openai-agents 0.0.6
with Z.AI glm-5 (primary) and OpenRouter (fallback).
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional

from agents import Agent, FunctionTool, RunContextWrapper, RunHooks, Runner
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
    "getlyrics", "gh", "ghissue", "ghn", "ghnext", "gis",
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
    "Keep your final answer concise ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ IRC lines are short. "
    "When a tool returns raw text, extract and summarise the key information."
)

class _LoggingHooks(RunHooks):
    async def on_tool_start(self, context, agent, tool) -> None:
        logger.info("agent: tool invoked: %s", tool.name)


_HOOKS = _LoggingHooks()

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
        # internal_launch() would skip them ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ we want them enforced per tool call.
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


def _get_or_build_agent(bot, cfg: dict, tools: list[FunctionTool]) -> Agent:
    key = id(bot)
    if key in _AGENT_CACHE:
        return _AGENT_CACHE[key]
    instructions = cfg.get("instructions") or AGENT_INSTRUCTIONS
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
        logger.info("agent: aborting ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ enabled=%s backends=%s", cfg.get("enabled") if cfg else "N/A", bool(cfg.get("backends")) if cfg else "N/A")
        return

    enabled_chans = cfg.get("enabled_channels") or []
    if enabled_chans and event.chan not in enabled_chans:
        logger.info("agent: aborting ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ chan %s not in enabled_channels %s", event.chan, enabled_chans)
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
    try:
        last_err: Optional[BaseException] = None
        for backend in backends_to_try:
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
                        hooks=_HOOKS,
                        max_turns=max_turns,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError as e:
                logger.warning("agent: %s timed out after %ss", backend, timeout)
                last_err = e
                continue
            except Exception as e:
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

        err_name = type(last_err).__name__ if last_err else "unknown"
        event.reply(f"Agent failed: {err_name}")
    finally:
        await stop_typing_for_command(event.conn, target, typing_id)


@hook.command("ask", "agent", "agi", autohelp=False)
async def agent_command(text, event):
    """<prompt> - ask the bot in natural language; uses any available tool."""
    if not text:
        event.reply("usage: .ask <natural language prompt>")
        return
    await _run_agent(event, text)
