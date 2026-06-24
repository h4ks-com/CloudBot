"""Sandbox code execution agents for CloudBot.

Two commands backed by the remote code-sandbox MCP server:
  .prove <problem>  — Rocq/Coq theorem prover, iterates up to 8 turns
  .code  <task>     — write-run-fix coding agent, iterates up to 8 turns

Both reply immediately with a status line, then post the result when done.
Backend and model config is shared with plugins/agent.py (plugins.agent section).
Sandbox connection config lives in plugins.sandbox_agent section.
"""

import json
import logging
from datetime import datetime

import httpx
from agents import Agent, FunctionTool, RunContextWrapper

from cloudbot import hook
from cloudbot.agent.subagent import SubagentError, run_subagent
from cloudbot.util.typing import (
    start_typing_for_command,
    stop_typing_for_command,
)
from plugins.agent import _format_answer

logger = logging.getLogger("cloudbot")

PROVE_INSTRUCTIONS = (
    "You are a formal theorem prover using Coq 8.9.0 (standard library only — no mathcomp).\n"
    "\n"
    "STRATEGY: Write a COMPLETE, self-contained proof, run it once to verify. "
    "If it fails, read ALL errors, then REWRITE THE ENTIRE PROOF from scratch — do NOT patch incrementally.\n"
    "\n"
    "SANDBOX MEMORY LIMIT — CRITICAL RULES:\n"
    "- NEVER `Require Import Coq.omega.Omega` — it OOM-kills.\n"
    "- NEVER `Require Import Coq.Arith.Arith` — redundant (ZArith includes it) and wastes memory.\n"
    "- NEVER use tactics: `lia`, `linarith`, `nlinarith`, `psatz`, `omega` — all OOM.\n"
    "- NEVER use multi-step rewrite chains (Z.mul_assoc/Z.mul_comm chains) — huge proof terms, OOM.\n"
    "- ONLY import: `Require Import Coq.ZArith.ZArith`.\n"
    "- Budget: ZArith uses ~250MB. Max ~4 global Lemma/Theorem blocks.\n"
    "- Prefer 1-2 global lemmas + 1 main theorem; use `assert` for local helpers.\n"
    "\n"
    "USE `ring` FOR ALL POLYNOMIAL ARITHMETIC:\n"
    "- `ring` proves equalities like `(2*k)*(2*k) = 2*(2*(k*k))` in ONE tactic.\n"
    "- `ring` uses computational reflection → tiny proof term, NO memory danger.\n"
    "- ALWAYS prefer `ring` over manual rewrite chains. Pattern:\n"
    "  `assert (H : LHS = RHS) by ring.`  then use H.\n"
    "\n"
    "KEY LEMMAS (all from Coq.ZArith.ZArith):\n"
    "- `Z.even_mul`: Z.even (a*b) = Z.even a || Z.even b\n"
    "- `Z.even_spec n`: Z.even n = true <-> (2 | n), where (2 | n) = exists k, n = 2*k\n"
    "  Extract k: `destruct (proj1 (Z.even_spec p) Heven) as [k Hk]` → Hk : p = 2*k\n"
    "- `Z.mul_cancel_l p n m`: p<>0 -> (p*n=p*m <-> n=m)\n"
    "  Usage: `apply (proj1 (Z.mul_cancel_l 2 _ _ ltac:(intro H; discriminate))) in Heq`\n"
    "- `Z.abs_mul a b`: Z.abs (a*b) = Z.abs a * Z.abs b\n"
    "- `Z.abs_nonneg n`: 0 <= Z.abs n\n"
    "- `Z.abs_pos n`: 0 < Z.abs n <-> n <> 0\n"
    "- `Z.lt_add_pos_r n m`: 0 < m -> n < n + m\n"
    "- `Z2Nat.inj_lt n m Hn Hm`: 0<=n -> 0<=m -> (Z.to_nat n < Z.to_nat m <-> n < m)\n"
    "- `2 <> 0`: prove with `intro H; discriminate`\n"
    "- `reflexivity`, `congruence`, `auto`, `simpl`, `f_equal` — all safe\n"
    "\n"
    "DESCENT/INDUCTION PATTERN (for irrationality proofs):\n"
    "- `lt_wf_ind : forall n P, (forall n0, (forall m, m<n0 -> P m) -> P n0) -> P n`\n"
    "- Setup: `remember (Z.to_nat (Z.abs p)) as n eqn:Hn; revert p q Hn;`\n"
    "  `induction n using lt_wf_ind; intros p q Hn IH Hq Heq.`\n"
    "- Descent when p=2*k and p<>0 → |k| < |p|:\n"
    "  (1) k<>0: `intro H0; apply Hpne; rewrite Hk, H0; ring`\n"
    "  (2) Z.abs p = 2*Z.abs k: `rewrite Hk, Z.abs_mul; simpl`\n"
    "  (3) Z.abs k < Z.abs p: rewrite (2); apply Z.lt_add_pos_r; apply (proj1 (Z.abs_pos k) Hkne)\n"
    "  (4) nat lt: `proj2 (Z2Nat.inj_lt _ _ (Z.abs_nonneg k) (Z.abs_nonneg p)) Hlt`\n"
    "\n"
    "SQRT(2) PROOF STRUCTURE:\n"
    "  Theorem: forall p q : Z, q <> 0 -> p * p <> 2 * (q * q).\n"
    "  1. `remember (Z.to_nat (Z.abs p)) as n; revert p q Hn;`\n"
    "     `induction n using lt_wf_ind; intros p q Hn IH Hqne Heq.`\n"
    "  2. p even: Z.even(p*p)=true (from Heq since 2|p*p); Z.even_mul → Z.even p=true\n"
    "     → `destruct (proj1 (Z.even_spec p) Hpeven) as [k Hk]`\n"
    "  3. q*q=2*(k*k): rewrite Hk in Heq;\n"
    "     `assert Hring : (2*k)*(2*k) = 2*(2*(k*k)) by ring;`\n"
    "     rewrite Hring in Heq; cancel 2 via Z.mul_cancel_l → `Heq : 2*(k*k) = q*q`\n"
    "  4. q even → q=2*m by same argument; then k*k=2*(m*m) by same algebra\n"
    "  5. p<>0: if p=0 then Heq → q=0, contradicting Hqne\n"
    "  6. Apply IH: `IH (Z.to_nat (Z.abs k)) Hnat_lt k m eq_refl Hmne Heq3`\n"
    "     where Hnat_lt comes from |k| < |p| via descent steps (1)-(4) above\n"
    "\n"
    "- exit_code=0 + '(no output, exit code 0)' = proof succeeded.\n"
    "- Any 'Error:' in output = failure — fix everything at once.\n"
    "\n"
    "When done, reply with the working Coq code block and a 1-2 sentence plain English explanation."
)

CODE_INSTRUCTIONS = (
    "You are a coding assistant with access to an isolated sandbox. "
    "The sandbox is for small, self-contained code snippets — use it to verify correctness, "
    "test an algorithm, or demonstrate something you're unsure about. "
    "Not for downloading packages, building projects, or long-running computations.\n"
    "Available languages: python (numpy/pandas/scipy/sympy), javascript (node), bash, "
    "c, c++ (gcc 10), rust, go, lua, coq.\n"
    "Prefer python for math/data, rust for performance, javascript for web/parsing, bash for system tasks. "
    "Write a minimal working snippet, run it, fix any errors. "
    "Show the final code and a brief explanation. "
    "If the task needs unavailable libraries or is too large for a snippet, say so clearly."
)


class _AgentState:
    prove_agent: Agent | None = None
    code_agent: Agent | None = None


async def _mcp_init(client: httpx.AsyncClient, url: str, api_key: str) -> str:
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    resp = await client.post(
        url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cloudbot", "version": "1.0"},
            },
        },
    )
    resp.raise_for_status()
    session_id = resp.headers.get("mcp-session-id", "")
    if not session_id:
        raise ValueError("sandbox MCP server returned no session ID")
    return session_id


async def _mcp_call(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    session_id: str,
    tool: str,
    args: dict,
) -> str:
    headers = {
        "apikey": api_key,
        "mcp-session-id": session_id,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    resp = await client.post(
        url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
    )
    resp.raise_for_status()
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            if "error" in data:
                return f"Error: {data['error'].get('message', data['error'])}"
            content = data.get("result", {}).get("content", [])
            if content:
                return content[0].get("text", "(no output)")
    return "(no output)"


def _make_sandbox_tools(sandbox_url: str, api_key: str) -> list[FunctionTool]:
    async def _ensure_session(
        ctx: RunContextWrapper,
    ) -> tuple[httpx.AsyncClient, str]:
        # ctx.context is a fresh dict per run — session lives there, not in closure.
        run_state = ctx.context
        if "client" not in run_state:
            client = httpx.AsyncClient(timeout=60.0)
            session_id = await _mcp_init(client, sandbox_url, api_key)
            run_state["client"] = client
            run_state["session_id"] = session_id
        return run_state["client"], run_state["session_id"]

    async def run_code(ctx: RunContextWrapper, args_json: str) -> str:
        args = json.loads(args_json) if args_json else {}
        language = str(args.get("language", "python"))
        code = str(args.get("code", ""))
        stdin = str(args.get("stdin", ""))
        client, session_id = await _ensure_session(ctx)
        return await _mcp_call(
            client,
            sandbox_url,
            api_key,
            session_id,
            "run_code",
            {
                "language": language,
                "code": code,
                "stdin": stdin,
            },
        )

    async def list_languages(ctx: RunContextWrapper, _args_json: str) -> str:
        client, session_id = await _ensure_session(ctx)
        return await _mcp_call(
            client, sandbox_url, api_key, session_id, "list_languages", {}
        )

    return [
        FunctionTool(
            name="run_code",
            description=(
                "Execute code in an isolated sandbox. "
                "Available: python (numpy/pandas/scipy/sympy), javascript (node 20), bash, "
                "c, c++ (gcc 10), rust, go, lua, coq. "
                "For coq: exit_code=0 + empty output = proof succeeded; exit_code=1 = failure with error in output. "
                "Returns stdout+stderr combined."
            ),
            params_json_schema={
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "description": "Language name, e.g. 'python', 'coq', 'rust', 'javascript'",
                    },
                    "code": {
                        "type": "string",
                        "description": "Source code to execute",
                    },
                    "stdin": {
                        "type": "string",
                        "description": "Optional stdin to pass to the program",
                    },
                },
                "required": ["language", "code"],
            },
            on_invoke_tool=run_code,
        ),
        FunctionTool(
            name="list_languages",
            description="List all available programming languages in the sandbox.",
            params_json_schema={"type": "object", "properties": {}},
            on_invoke_tool=list_languages,
        ),
    ]


def _get_agents(cfg: dict) -> tuple[Agent, Agent]:
    if _AgentState.prove_agent is None or _AgentState.code_agent is None:
        sandbox_url = cfg.get("sandbox_url", "https://sandbox.t3ks.com/mcp")
        api_key = cfg.get("sandbox_api_key", "")
        tools = _make_sandbox_tools(sandbox_url, api_key)
        _AgentState.prove_agent = Agent(
            name="RocqProver", instructions=PROVE_INSTRUCTIONS, tools=tools
        )
        _AgentState.code_agent = Agent(
            name="CodeAgent", instructions=CODE_INSTRUCTIONS, tools=tools
        )
    return _AgentState.prove_agent, _AgentState.code_agent


async def _run_sandbox_agent(agent_type: str, event, prompt: str) -> None:
    bot = event.bot
    agent_cfg = (bot.config.get("plugins") or {}).get("agent") or {}
    sandbox_cfg = (bot.config.get("plugins") or {}).get("sandbox_agent") or {}

    if not agent_cfg.get("enabled", False) or not agent_cfg.get("backends"):
        event.reply("Agent not configured.")
        return

    prove_agent, code_agent = _get_agents(sandbox_cfg)
    agent = prove_agent if agent_type == "prove" else code_agent

    max_turns = int(
        sandbox_cfg.get(
            "prove_max_turns" if agent_type == "prove" else "code_max_turns",
            20 if agent_type == "prove" else 10,
        )
    )
    timeout = float(sandbox_cfg.get("timeout_s", 300))

    ts = datetime.now().strftime("%H:%M:%S")
    enriched = (
        f"[channel: {event.chan} | user: {event.nick} | time: {ts}]\n{prompt}"
    )

    typing_id = id(event)
    target = event.chan or event.nick
    await start_typing_for_command(event.conn, target, typing_id)
    try:
        answer = await run_subagent(
            bot,
            agent=agent,
            prompt=enriched,
            max_turns=max_turns,
            timeout_s=timeout,
        )
        event.reply(_format_answer(answer, agent_cfg))
    except SubagentError as e:
        event.reply(f"Agent failed: {e}")
    finally:
        await stop_typing_for_command(event.conn, target, typing_id)


@hook.command("prove", "pv", autohelp=False)
async def prove_command(text, event):
    """<theorem> - prove a mathematical theorem step by step using Coq/Rocq."""
    if not text:
        event.reply("usage: .prove <theorem or math problem>")
        return
    event.reply("Working on the proof, this may take a minute...")
    await _run_sandbox_agent("prove", event, text)


@hook.command("code", "cd", autohelp=False)
async def code_command(text, event):
    """<task> - write, run, and verify code in any supported language."""
    if not text:
        event.reply("usage: .code <coding task>")
        return
    event.reply("Writing and testing code...")
    await _run_sandbox_agent("code", event, text)
