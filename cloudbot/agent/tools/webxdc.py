"""Webxdc app deployment tool.

Packages agent-generated HTML into a .xdc bundle (ZIP with manifest.toml +
index.html) and uploads via the standard pastebin. ObsidianIRC clients
(and other webxdc-capable hosts) recognise the .xdc extension and render
the result as a sandboxed, multi-user interactive widget inline in chat.

Webxdc spec: https://webxdc.org/docs/
"""

import io
import zipfile

import requests

from cloudbot.agent.common import run_in_executor
from cloudbot.agent.registry import tool
from cloudbot.util import web

# Description doubles as inline documentation for the model. Spelling out the
# webxdc API surface here lets the agent generate correct apps in one shot
# without round-tripping for clarification.
_WEBXDC_DESCRIPTION = (
    "Create and deploy a multi-user 'webxdc' app, then return a .xdc URL that "
    "ObsidianIRC and other webxdc-capable chat clients render INLINE as an "
    "interactive widget. State syncs across all chat members automatically.\n\n"
    "WHEN TO USE: user asks for an inline poll/voting widget, shared checklist, "
    "collaborative whiteboard, group decision tool, multiplayer mini-game, or "
    "anything where multiple chat users need to see and change shared state. "
    "Trigger phrases: 'inline poll', 'inline app', 'show in chat', 'widget', "
    "'shared X', 'multiplayer X', 'create a poll/checklist/game in chat'. "
    "Prefer this over web_app whenever the result is meant to be collaborative.\n\n"
    "WEBXDC RUNTIME (window.webxdc, provided by host):\n"
    "  selfAddr (string)   — identifies this peer (use as voter/owner id)\n"
    "  selfName (string)   — display name of this peer\n"
    "  sendUpdate({payload, info?, summary?, document?, href?}, descr?)\n"
    "    payload   — any JSON value; broadcast to all peers (≤3KB practical limit)\n"
    "    info      — short status line shown in chat next to the app\n"
    "    summary   — ~20-char headline\n"
    "  setUpdateListener(callback, serial=0)\n"
    "    callback gets {serial, max_serial, payload, info?, summary?}\n"
    "    treat updates as APPEND-ONLY LOG; replay rebuilds state from scratch\n"
    "    pass serial=0 first time; the host replays everything since then\n\n"
    "RULES for the html field:\n"
    "1. Single self-contained file. ALL CSS in <style>, ALL JS in <script>. "
    "No external resources except <script src='webxdc.js'></script> in <head> "
    "(host injects the shim — never package webxdc.js yourself).\n"
    "2. NO network: fetch/XHR/WebSocket/CDN are blocked by sandbox CSP. "
    "Don't reference fonts/images/scripts from outside the document.\n"
    "3. Every state mutation must go through sendUpdate so peers stay in sync. "
    "Don't store state in localStorage alone — peers won't see it.\n"
    "4. ★ APPEND-ONLY DELTAS — THIS IS THE #1 BUG IN MULTIPLAYER APPS ★\n"
    "   Each sendUpdate payload must describe ONE atomic action (one move, one "
    "vote, one toggle). NEVER send a snapshot of the full game state.\n"
    "   DON'T: sendUpdate({payload:{type:'state', moves:allMoves, board:grid, turn:t}})\n"
    "   DO:    sendUpdate({payload:{type:'move', addr:selfAddr, col:3}})\n"
    "   In setUpdateListener, APPEND the delta to local state (push to moves[], "
    "set votes[addr]=opt, etc.). Then derive the visible state from the log. "
    "   Why: two peers move concurrently → both broadcast snapshots based on "
    "their stale view → last-write-wins overwrites the other's move. Deltas "
    "merge cleanly because each one only adds, never replaces.\n"
    "   For turn-based games (chess, connect4, tic-tac-toe): sender ALSO appends "
    "their own move to local state immediately and broadcasts that single move; "
    "do NOT also re-send already-known moves.\n"
    "5. Player assignment: don't decide locally 'I am player 1'. Derive from "
    "the update log — first unique selfAddr to send a join/move = player 1, "
    "second unique = player 2. Everyone replays the log identically and reaches "
    "the same assignment. Using local guess gives both peers 'player 1'.\n"
    "6. Use selfAddr for identity (votes, ownership) — NOT random IDs that won't "
    "match across reloads.\n"
    "7. Keep it tight: ~150-300 lines, mobile-friendly, system fonts, simple CSS.\n"
    "8. Don't request internet access; don't use eval; don't use document.write.\n\n"
    "AFTER calling this tool: the URL it returns IS the assistant's final answer. "
    "Reply with just the URL (one line, no preamble, no explanation). The IRC client "
    "renders the app inline and the user sees a chat embed — extra prose is noise.\n\n"
    "EXAMPLE skeleton (poll):\n"
    "<!DOCTYPE html><html><head><meta charset='utf-8'>\n"
    "<script src='webxdc.js'></script>\n"
    "<style>body{font:14px system-ui;padding:1em;max-width:30em;margin:auto}\n"
    "button{padding:.5em 1em;margin:.2em}.vote{display:flex;gap:1em}</style>\n"
    "</head><body><h2>Poll</h2><div id='opts'></div><div id='res'></div>\n"
    "<script>\n"
    "const OPTIONS = ['Pizza','Sushi','Tacos'];\n"
    "const votes = {};  // addr -> option\n"
    "function render(){\n"
    "  document.getElementById('opts').innerHTML = OPTIONS.map(o=>\n"
    "    `<button onclick=\"vote('${o}')\">${o}</button>`).join('');\n"
    "  const tally = {}; OPTIONS.forEach(o=>tally[o]=0);\n"
    "  Object.values(votes).forEach(o=>{ if(tally[o]!==undefined) tally[o]++; });\n"
    "  document.getElementById('res').innerHTML = OPTIONS.map(o=>\n"
    "    `<div>${o}: ${tally[o]}</div>`).join('');\n"
    "}\n"
    "function vote(opt){\n"
    "  window.webxdc.sendUpdate({payload:{addr:window.webxdc.selfAddr,opt},\n"
    "    info:`${window.webxdc.selfName} voted ${opt}`}, 'vote');\n"
    "}\n"
    "window.webxdc.setUpdateListener(u=>{\n"
    "  if(u.payload && u.payload.addr) votes[u.payload.addr]=u.payload.opt;\n"
    "  render();\n"
    "}, 0);\n"
    "render();\n"
    "</script></body></html>\n"
)

def _strip_md_fences(html: str) -> str:
    """Strip surrounding ``` fences if the model wrapped output as a code block."""
    s = html.strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_xdc(html: str, name: str, description: str) -> bytes:
    manifest_lines = [f'name = "{_toml_escape(name)}"']
    if description:
        manifest_lines.append(f'description = "{_toml_escape(description)}"')
    manifest_lines.append("min_api = 1")
    manifest = "\n".join(manifest_lines) + "\n"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.toml", manifest)
        zf.writestr("index.html", html)
    return buf.getvalue()


def _upload_xdc(html: str, name: str, description: str) -> str:
    xdc_bytes = _build_xdc(_strip_md_fences(html), name, description)
    return web.paste(xdc_bytes, ext="xdc")


@tool(
    name="webxdc_app",
    description=_WEBXDC_DESCRIPTION,
    schema={
        "type": "object",
        "properties": {
            "html": {
                "type": "string",
                "description": (
                    "Self-contained HTML using window.webxdc API. Must include "
                    "<script src='webxdc.js'></script>. No external resources, "
                    "no network calls, all state changes via sendUpdate."
                ),
            },
            "name": {
                "type": "string",
                "description": "Short app name (e.g. 'Pizza Poll', 'Todo List').",
            },
            "description": {
                "type": "string",
                "description": "Optional one-line description shown in app preview.",
            },
        },
        "required": ["html", "name"],
    },
)
async def webxdc_app(ctx, data):
    html = str(data.get("html") or "").strip()
    name = str(data.get("name") or "App").strip()[:60] or "App"
    description = str(data.get("description") or "").strip()[:200]
    if not html:
        return "(error: html required)"
    if "webxdc" not in html.lower():
        return (
            "(error: html does not reference window.webxdc — webxdc apps must "
            "use sendUpdate/setUpdateListener for collaboration. Add "
            "<script src='webxdc.js'></script> and use the API.)"
        )
    try:
        url = await run_in_executor(_upload_xdc, html, name, description)
    except (requests.RequestException, OSError, ValueError, RuntimeError) as e:
        return f"(error uploading webxdc: {e})"
    return url
