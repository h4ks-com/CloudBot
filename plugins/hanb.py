# hanb - toy universe modeling language
# https://github.com/handyc/hanb
# Author: CloudBot
# Date: 2026-05-03

import json
import os
import random
from pathlib import Path

from cloudbot import hook

# ---------------------------------------------------------------------------
# Hex board templates (rotation 0 = edge-up, rotation 1 = point-up)
# ---------------------------------------------------------------------------
EDGE_BOARD = [
    "         {a}   {b}   {c}   {d}   {e}         ",
    "       {f}   {g}   {h}   {i}   {j}   {k}       ",
    "     {l}   {m}   {n}   {o}   {p}   {q}   {r}     ",
    "   {s}   {t}   {u}   {v}   {w}   {x}   {y}   {z}   ",
    " {A}   {B}   {C}   {D}   {E}   {F}   {G}   {H}   {I}   ",
    "   {J}   {K}   {L}   {M}   {N}   {O}   {P}   {Q}   ",
    "     {R}   {S}   {T}   {U}   {V}   {W}   {X}     ",
    "       {Y}   {Z}   {0}   {1}   {2}   {3}       ",
    "         {4}   {5}   {6}   {7}   {8}         ",
]

POINT_BOARD = [
    "                     {a}                     ",
    "                {b}         {c}                ",
    "           {d}         {e}         {f}           ",
    "      {g}         {h}         {i}         {j}      ",
    " {k}         {l}         {m}         {n}         {o} ",
    "      {p}         {q}         {r}         {s}      ",
    " {t}         {u}         {v}         {w}         {x} ",
    "      {y}         {z}         {A}         {B}      ",
    " {C}         {D}         {E}         {F}         {G} ",
    "      {H}         {I}         {J}         {K}      ",
    " {L}         {M}         {N}         {O}         {P} ",
    "      {Q}         {R}         {S}         {T}      ",
    " {U}         {V}         {W}         {X}         {Y} ",
    "      {Z}         {0}         {1}         {2}      ",
    "           {3}         {4}         {5}           ",
    "                {6}         {7}                ",
    "                     {8}                     ",
]

HANB_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-."
DATA_DIR = Path(__file__).parent / "data" / "hanb"


def _load_json(filename):
    """Load a JSON file from the hanb data directory."""
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _discover_categories():
    """Dynamically discover all hanb data files and their categories."""
    categories = {}
    if not DATA_DIR.exists():
        return categories
    for json_file in sorted(DATA_DIR.glob("*.json")):
        name = json_file.stem  # e.g. 'scales', 'boards', 'facts'
        data = _load_json(json_file.name)
        if data:
            categories[name] = data
    return categories


def render_board(board_str, rotation=0):
    """Render a 61-char hanb board string into text lines."""
    board_str = board_str.ljust(61, "a")[:61]
    template = EDGE_BOARD if rotation == 0 else POINT_BOARD
    keys = list(HANB_ALPHABET[:61])
    values = list(board_str)
    mapping = dict(zip(keys, values))
    return [line.format(**mapping) for line in template]


def render_board_inline(board_str):
    """Render a compact single-line representation of a board."""
    board_str = board_str.ljust(61, "a")[:61]
    # Show the board as a compressed string plus a small summary
    non_foam = sum(1 for c in board_str if c != "a")
    unique = len(set(board_str))
    return f"[{board_str}] ({non_foam} non-foam cells, {unique} unique values)"


def get_scale_info(char):
    """Get scale information for a hanb character."""
    scales = _load_json("scales.json")
    if char in scales:
        s = scales[char]
        desc = s["description"]
        if desc:
            return f"\x02{char}\x02 = {s['name']}: {desc}"
        return f"\x02{char}\x02 = {s['name']}"
    return f"\x02{char}\x02 is not a recognized hanb scale character."


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@hook.command("hanb", autohelp=False)
def hanb(text, reply, notice):
    """[board <name>|scale <char>|fact|random|render <board_str> [rotation]|categories|list <category>] -
    Explore the hanb toy universe modeling language. https://github.com/handyc/hanb"""
    if not text.strip():
        return hanb_fact.__wrapped__(reply)

    parts = text.strip().split(None, 1)
    subcmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if subcmd == "board":
        return hanb_board.__wrapped__(args, reply)
    elif subcmd == "scale":
        return hanb_scale.__wrapped__(args, reply)
    elif subcmd == "fact":
        return hanb_fact.__wrapped__(reply)
    elif subcmd == "random":
        return hanb_random.__wrapped__(reply)
    elif subcmd == "render":
        return hanb_render.__wrapped__(args, reply)
    elif subcmd == "categories":
        return hanb_categories.__wrapped__(reply)
    elif subcmd == "list":
        return hanb_list.__wrapped__(args, reply)
    else:
        # Try to interpret as a board string or scale char
        if len(subcmd) == 1:
            return hanb_scale.__wrapped__(subcmd, reply)
        notice("Unknown subcommand. See .hanb for usage.")
        return None


@hook.command("hanbboard", autohelp=False)
def hanb_board(text, reply):
    """[<name>] - Display a named hanb board. Use .hanb list boards to see available boards."""
    boards = _load_json("boards.json")
    if not boards:
        return "No hanb board data available."

    if not text.strip():
        # List all boards grouped by category
        lines = []
        for category, items in boards.items():
            names = list(items.keys())
            lines.append(f"\x02{category}\x02: {', '.join(names)}")
        return "Available boards: " + " | ".join(lines)

    query = text.strip().lower()
    # Search across all categories
    found = []
    for category, items in boards.items():
        for name, data in items.items():
            if query in name.lower():
                found.append((category, name, data))

    if not found:
        return f"No board found matching '\x02{query}\x02'. Use .hanb list boards to see available boards."

    # Show the first match
    cat, name, data = found[0]
    board_str = data["board"]
    lines = render_board(board_str, rotation=0)
    header = f"\x02{name}\x02 ({cat})"
    if data.get("description"):
        header += f" -- {data['description']}"
    return "\n".join([header] + lines)


@hook.command("hanbscale", autohelp=False)
def hanb_scale(text, reply):
    """<char> - Show information about a hanb scale character (a-z, A-Z, 0-9, -, .)."""
    if not text.strip():
        scales = _load_json("scales.json")
        if not scales:
            return "No scale data available."
        # Show all scales grouped by range
        groups = {
            "subatomic": list("abcdefghijklmnopqrstuvwxy"),
            "atomic→human": list("zABCDEFGHIJK"),
            "geographic": list("LMNOPQRST"),
            "astronomical": list("UVWXYZ0123"),
            "cosmic": list("456789-."),
        }
        lines = []
        for group_name, chars in groups.items():
            names = []
            for c in chars:
                if c in scales:
                    names.append(f"{c}:{scales[c]['name']}")
            lines.append(f"\x02{group_name}\x02: {' | '.join(names)}")
        return "Hanb scales (each step = 10x): " + " || ".join(lines)

    char = text.strip()[0]
    return get_scale_info(char)


@hook.command("hanbfact", autohelp=False)
def hanb_fact(reply):
    """- Get a random hanb fact."""
    facts = _load_json("facts.json")
    if not facts:
        return "No hanb facts available."
    return random.choice(facts)


@hook.command("hanbrandom", autohelp=False)
def hanb_random(reply):
    """- Generate a random hanb board and display it."""
    # Generate a random board string
    board_chars = []
    # Make it somewhat interesting: mostly 'a' (quantum foam) with some structure
    for i in range(61):
        r = random.random()
        if r < 0.6:
            board_chars.append("a")
        elif r < 0.85:
            board_chars.append(random.choice("bcdefghijklmnopqrstuvwxyz"))
        elif r < 0.95:
            board_chars.append(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        else:
            board_chars.append(random.choice("0123456789-."))

    board_str = "".join(board_chars)
    lines = render_board(board_str, rotation=random.choice([0, 1]))
    non_foam = sum(1 for c in board_str if c != "a")
    unique = len(set(board_str))
    scales = _load_json("scales.json")
    # Find the highest non-foam scale
    max_char = max(board_str, key=lambda c: HANB_ALPHABET.index(c) if c in HANB_ALPHABET else 0)
    max_name = scales.get(max_char, {}).get("name", "unknown") if max_char in scales else "unknown"

    header = f"\x02Random hanb board\x02 (rotation {'edge' if lines == render_board(board_str, 0) else 'point'})"
    header += f" -- {non_foam} objects, highest scale: {max_char} ({max_name})"
    return "\n".join([header] + lines + [render_board_inline(board_str)])


@hook.command("hanbrender", autohelp=False)
def hanb_render(text, reply):
    """<board_string> [rotation] - Render a 61-char hanb board string. rotation: 0=edge, 1=point."""
    if not text.strip():
        return "Usage: .hanbrender <61-char-board-string> [0|1]"

    parts = text.strip().split()
    board_str = parts[0]
    rotation = 0
    if len(parts) > 1:
        try:
            rotation = int(parts[1])
            if rotation not in (0, 1):
                rotation = 0
        except ValueError:
            pass

    if len(board_str) < 61:
        board_str = board_str.ljust(61, "a")
    elif len(board_str) > 61:
        board_str = board_str[:61]

    lines = render_board(board_str, rotation)
    return "\n".join(lines + [render_board_inline(board_str)])


@hook.command("hanbcategories", autohelp=False)
def hanb_categories(reply):
    """- List all available hanb data categories (dynamically loaded)."""
    categories = _discover_categories()
    if not categories:
        return "No hanb data categories found."

    lines = [f"\x02{len(categories)} hanb data categories loaded:\x02"]
    for name, data in categories.items():
        if isinstance(data, dict):
            count = len(data)
            if all(isinstance(v, dict) for v in data.values()):
                # Nested structure
                total = sum(len(v) for v in data.values())
                lines.append(f"  \x02{name}\x02: {count} subcategories, {total} total entries")
            else:
                lines.append(f"  \x02{name}\x02: {count} entries")
        elif isinstance(data, list):
            lines.append(f"  \x02{name}\x02: {len(data)} entries (list)")
    return "\n".join(lines)


@hook.command("hanblist", autohelp=False)
def hanb_list(text, reply):
    """[<category>] - List entries in a hanb data category. No arg = list categories."""
    categories = _discover_categories()
    if not categories:
        return "No hanb data categories found."

    if not text.strip():
        return hanb_categories.__wrapped__(reply)

    query = text.strip().lower()
    if query not in categories:
        matches = [n for n in categories if query in n]
        if matches:
            query = matches[0]
        else:
            return f"Category '\x02{query}\x02' not found. Use .hanbcategories to see available categories."

    data = categories[query]
    if isinstance(data, dict):
        # Check if nested (like boards.json with subcategories)
        first_val = next(iter(data.values()), None)
        if isinstance(first_val, dict):
            lines = [f"\x02{query}\x02 entries:"]
            for subcat, items in data.items():
                names = list(items.keys())
                lines.append(f"  \x02{subcat}\x02: {', '.join(names)}")
            return "\n".join(lines)
        else:
            # Flat dict (like scales.json)
            lines = [f"\x02{query}\x02 entries ({len(data)} total):"]
            items = list(data.items())
            # Show in chunks for IRC
            chunk = []
            for k, v in items:
                if isinstance(v, dict):
                    name = v.get("name", k)
                    chunk.append(f"{k}:{name}")
                else:
                    chunk.append(f"{k}:{v}")
            return lines[0] + " | " + " | ".join(chunk[:20]) + (f" ... and {len(chunk)-20} more" if len(chunk) > 20 else "")
    elif isinstance(data, list):
        return f"\x02{query}\x02: {len(data)} entries. Use .hanbfact for a random one."
    return f"\x02{query}\x02: {len(data)} entries."
