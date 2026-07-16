"""Kaggle notebook sub-agent for CloudBot.

  .kaggle <request>  — write a Python notebook, run it on Kaggle's free compute,
                       and reply with the notebook URL plus its artifacts/log.
  .kquota            — remaining weekly GPU/TPU quota.

Runs on the main agent's model and backend (via ``run_subagent``) with its own
turn/time budget, so a multi-step write→run→inspect→fix loop can't blow the main
``.agi`` loop's much smaller cap.

The main agent does not delegate here — it holds the same kaggle_* tools directly
and uses this command's larger budget only when a job needs it.
"""

import re

from agents import Agent

from cloudbot import hook
from cloudbot.agent.common import run_in_executor
from cloudbot.agent.kaggle_client import (
    KaggleError,
    KaggleNotConfigured,
    quota,
    token_from_bot,
)
from cloudbot.agent.registry import build_custom_tools
from cloudbot.agent.subagent import SubagentError, ToolStepSink, run_subagent
from cloudbot.agent.tools.kaggle import LastRun, format_quota, last_run
from cloudbot.bot import CloudBot
from cloudbot.event import CommandEvent
from cloudbot.util import colors
from cloudbot.util.ai_common import format_reply_lines
from cloudbot.util.typing import (
    start_typing_for_command,
    stop_typing_for_command,
)
from plugins.core import bot_cmds

_URL_RE = re.compile(r"https?://\S+")

_TOOL_NAMES = frozenset(
    {
        "kaggle_quota",
        "kaggle_run_notebook",
        "kaggle_wait_for_notebook",
        "kaggle_notebook_status",
        "kaggle_notebook_output",
        "kaggle_list_notebooks",
        "kaggle_delete_notebook",
        "paste_markdown",
    }
)

# The Kaggle rules the model must follow live in the kaggle_* tool descriptions,
# which it reads at call time — repeating them here would fork one live external
# contract across two files. This covers only how to run the job.
KAGGLE_INSTRUCTIONS = """You are KaggleRunner. You write small Python programs, run them on \
Kaggle's free compute, and report what actually happened. Follow each tool's description
exactly — they carry the rules Kaggle imposes.

How to work:
1. Understand the request. Check kaggle_list_notebooks first and update an existing notebook
   rather than making a near-duplicate.
2. Write straightforward, self-contained Python. Print what matters — the log is how you and
   the user see results. Save real outputs (files, plots, JSON) where the tool tells you to.
3. Run it. If it is still going, call kaggle_wait_for_notebook — it waits for you. NEVER loop on
   kaggle_notebook_status or kaggle_notebook_output: every call costs a turn, and you will run
   out of turns long before the notebook finishes.
4. If it fails, read the log, fix the code, and run again under the SAME title.
5. Report: one short summary of what it did and what it found, and share the file the user asked
   for with kaggle_notebook_output(share=...).

You are answering in a chat channel, so keep it to a few short lines.

Give a compact, factual answer. Do not invent results — only report what the log and artifacts
actually show, and never write out a URL you did not get from a tool."""


class _AgentState:
    agent: Agent | None = None


def _get_agent() -> Agent:
    if _AgentState.agent is not None:
        return _AgentState.agent
    tools = [t for t in build_custom_tools() if t.name in _TOOL_NAMES]
    if not tools:
        raise SubagentError("no kaggle tools registered")
    agent = Agent(
        name="KaggleRunner",
        instructions=KAGGLE_INSTRUCTIONS,
        tools=tools,
    )
    _AgentState.agent = agent
    return agent


def _run_limits(bot: CloudBot) -> tuple[int, float]:
    # A healthy run is write → run → maybe one fix → report. Kaggle's own queue can
    # add a minute, so the ceiling is generous while the turn cap keeps a looping
    # model bounded.
    cfg = (bot.config.get("plugins") or {}).get("kaggle_agent") or {}
    return int(cfg.get("max_turns", 20)), float(cfg.get("timeout_s", 600))


async def run_kaggle(
    bot: CloudBot,
    prompt: str,
    event: CommandEvent | None = None,
    on_tool_step: ToolStepSink | None = None,
) -> str:
    """Write+run a notebook on Kaggle and return a short result with URLs.

    Raises KaggleNotConfigured before spending a sub-agent run: the tools
    themselves only surface a missing token as a tool-result string, which the
    model would then try to work around.
    """
    token_from_bot(bot)
    agent = _get_agent()
    max_turns, timeout_s = _run_limits(bot)
    text = await run_subagent(
        bot,
        agent=agent,
        prompt=prompt,
        max_turns=max_turns,
        timeout_s=timeout_s,
        context=event,
        on_tool_step=on_tool_step,
    )
    return _build_reply(text, last_run(event))


def _build_reply(text: str, last: LastRun | None) -> str:
    """The model's answer, minus any URL it made up.

    A link a tool emitted is real and passes through untouched; anything else is
    invented, since the model cannot know a URL it was not given. The notebook
    link is appended only when the answer left it out — it is the point of the
    run, so it must never be missing.
    """
    if last is None or not last.url:
        return text or "(no result)"
    answer = _URL_RE.sub(
        lambda m: m.group(0) if m.group(0) in last.known_urls else "",
        text or "",
    ).strip()
    if last.url in answer:
        return answer
    return "\n".join(
        [answer, colors.parse(f"$(bold)notebook$(clear) {last.url}")]
    )


@hook.command("kquota", "kagglequota", autohelp=False, allow_private=False)
async def kaggle_quota_command(bot: CloudBot) -> str:
    """- remaining Kaggle GPU/TPU quota for the week. CPU runs are unmetered."""
    try:
        token = token_from_bot(bot)
        report = await run_in_executor(quota, token)
    except KaggleNotConfigured:
        return "Kaggle not configured."
    except KaggleError as e:
        return f"Kaggle error: {e}"
    return format_quota(report)


@hook.command("kaggle", autohelp=False, allow_private=False)
async def kaggle_command(text, event):
    """<request> - write and run a Python notebook on Kaggle's free compute; returns the notebook URL and results."""
    if not text:
        event.reply(
            "usage: .kaggle <what you want computed / built / analysed>"
        )
        return
    # Deliberately vague: this command also lists, polls, shares and deletes, so
    # naming an action here would be wrong more often than right.
    event.reply("On it, this may take a minute...")
    typing_id = id(event)
    target = event.chan or event.nick
    await start_typing_for_command(event.conn, target, typing_id)
    workflow_id = bot_cmds.start_tool_workflow(
        event.conn, target, "kaggle", event.tag_value("msgid")
    )
    try:
        answer = await run_kaggle(
            event.bot,
            text,
            event=event,
            on_tool_step=bot_cmds.tool_step_sink(
                event.conn, target, workflow_id
            ),
        )
    except KaggleNotConfigured:
        event.reply(
            "Kaggle not configured.",
            extra_tags=bot_cmds.workflow_terminal_tag(workflow_id, "failed"),
        )
        return
    except SubagentError as e:
        event.reply(
            f"Kaggle agent failed: {e}",
            extra_tags=bot_cmds.workflow_terminal_tag(workflow_id, "failed"),
        )
        return
    finally:
        await stop_typing_for_command(event.conn, target, typing_id)
    # One PRIVMSG cannot carry newlines, so a joined string would ship only its
    # first line — silently dropping the notebook and artifact links, which are
    # the point of the run. Each line has to be its own reply argument.
    event.reply(
        *format_reply_lines(answer),
        ping_own_line=True,
        extra_tags=bot_cmds.workflow_terminal_tag(workflow_id, "complete"),
    )
