"""
hanb - IRC command for the hanb hexagonal board universe modeling system.

hanb is a simple language for creating model universes at any scale.
It uses a 61-cell hexagonal board with 64 characters (a-z, A-Z, 0-9, -, .)
representing different spatial scales from Planck length to the entire universe.

Usage:
  .hanb                   - Show a random hanb board
  .hanb <category>        - List boards in a category
  .hanb <name>            - Show a specific board
  .hanb scale <char>      - Show what scale a hanb character represents
  .hanb time <char>       - Show what time scale a hanb character represents
  .hanb render <board>    - Render a 61-char board string as hex grid
  .hanb logic [n]          - Show hanb logic operation (0-15)
  .hanb categories        - List all board categories
  .hanb info              - Show hanb overview
  .hanb random            - Generate a random board
"""

import os
import random
import re
from pathlib import Path

from cloudbot import hook

# ---------------------------------------------------------------------------
# Dynamic loading of hanb data modules
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent / "hanb_data"
_BOARDS_CACHE = {}
_SCALES_CACHE = None


def _load_boards():
    """Dynamically load all board data from hanb_data package."""
    global _BOARDS_CACHE
    if _BOARDS_CACHE:
        return _BOARDS_CACHE

    # Import all .py files in hanb_data that define a BOARDS dict
    for fname in sorted(os.listdir(_DATA_DIR)):
        if fname.endswith(".py") and fname != "__init__.py":
            mod_name = f"plugins.hanb_data.{fname[:-3]}"
            try:
                mod = __import__(mod_name, fromlist=["BOARDS"])
                if hasattr(mod, "BOARDS"):
                    _BOARDS_CACHE.update(mod["BOARDS"])
            except Exception:
                pass

    return _BOARDS_CACHE


def _load_scales():
    """Load scale definitions."""
    global _SCALES_CACHE
    if _SCALES_CACHE:
        return _SCALES_CACHE
    try:
        mod = __import__("plugins.hanb_data.scales", fromlist=[
            "HANB_ALPHABET", "SPATIAL_SCALES", "TIME_SCALES",
            "LOGIC_OPERATIONS", "HEX_ROWS",
        ])
        _SCALES_CACHE = {
            "alphabet": mod.HANB_ALPHABET,
            "spatial": mod.SPATIAL_SCALES,
            "time": mod.TIME_SCALES,
            "logic": mod.LOGIC_OPERATIONS,
            "hex_rows": mod.HEX_ROWS,
        }
    except Exception:
        _SCALES_CACHE = {}
    return _SCALES_CACHE


# ---------------------------------------------------------------------------
# Board rendering
# ---------------------------------------------------------------------------

def render_board(board_str):
    """Render a 61-character hanb board string as a hex grid."""
    scales = _load_scales()
    hex_rows = scales.get("hex_rows", [
        (4, 5), (3, 6), (2, 7), (1, 8), (0, 9),
        (1, 8), (2, 7), (3, 6), (4, 5),
    ])

    if len(board_str) != 61:
        return f"Invalid board length ({len(board_str)}), expected 61"

    lines = []
    idx = 0
    for indent, count in hex_rows:
        prefix = " " * (indent * 2)
        cells = []
        for _ in range(count):
            if idx < len(board_str):
                cells.append(board_str[idx])
                idx += 1
            else:
                cells.append(" ")
        lines.append(prefix + "  ".join(cells))

    return "\n".join(lines)


def get_categories():
    """Get all board categories and their board names."""
    boards = _load_boards()
    categories = {}
    for name in sorted(boards.keys()):
        cat, _, board_name = name.partition("_")
        categories.setdefault(cat, []).append(board_name)
    return categories


def find_board(name):
    """Find a board by name, trying various matching strategies."""
    boards = _load_boards()

    # Exact match
    if name in boards:
        return name, boards[name]

    # Try with common prefixes
    for prefix in ["cosmic_", "planetary_", "geographic_", "city_",
                    "nature_", "object_", "creature_", "structure_",
                    "vehicle_", "weapon_", "magic_", "terrain_",
                    "element_", "abstract_", "food_"]:
        key = prefix + name
        if key in boards:
            return key, boards[key]

    # Case-insensitive search
    name_lower = name.lower()
    for key, val in boards.items():
        if key.lower() == name_lower:
            return key, val
        if key.lower().endswith(name_lower):
            return key, val

    # Partial match
    for key, val in boards.items():
        if name_lower in key.lower():
            return key, val

    return None, None


def generate_random_board():
    """Generate a random 61-character hanb board."""
    scales = _load_scales()
    alphabet = scales.get("alphabet", "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.")
    return "".join(random.choice(alphabet) for _ in range(61))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@hook.command("hanb")
def hanb_cmd(text, notice, message):
    """<name|category|scale <char>|time <char>|render <board>|logic [n]|categories|info|random> - hanb hexagonal board universe system"""
    if not text.strip():
        # No argument - show random board
        boards = _load_boards()
        if boards:
            name = random.choice(list(boards.keys()))
            board = boards[name]
            rendered = render_board(board)
            cat = name.split("_")[0] if "_" in name else "?"
            bname = name.split("_", 1)[1] if "_" in name else name
            message(f"[{cat}/{bname}]")
            for line in rendered.split("\n"):
                message(line)
            message(f"Board: {board}")
        else:
            message("No hanb boards loaded.")
        return

    parts = text.strip().split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "info":
        scales = _load_scales()
        alpha = scales.get("alphabet", "?")
        boards = _load_boards()
        cats = get_categories()
        message("hanb - hexagonal board universe modeling system")
        message(f"Alphabet ({len(alpha)} chars): {alpha}")
        message(f"Boards loaded: {len(boards)} across {len(cats)} categories")
        message(f"Categories: {', '.join(sorted(cats.keys()))}")
        message("Each board is a 61-cell hex grid. Characters represent spatial scales")
        message("from Planck length (b) to the entire universe (.)")
        message("Use .hanb <name> to view a board, .hanb categories to list all")

    elif cmd == "categories":
        cats = get_categories()
        if not cats:
            message("No categories found.")
            return
        for cat in sorted(cats.keys()):
            names = cats[cat]
            message(f"{cat}: {', '.join(names[:8])}" +
                   (f" (+{len(names)-8} more)" if len(names) > 8 else ""))

    elif cmd == "scale" or cmd == "spatial":
        if not arg:
            notice("Usage: .hanb scale <hanb_char>")
            return
        char = arg.strip()[0]
        scales = _load_scales()
        spatial = scales.get("spatial", {})
        if char in spatial:
            name, desc = spatial[char]
            label = f'"{name}" ' if name else ""
            message(f"Spatial scale '{char}': {label}({desc})")
        else:
            message(f"Unknown hanb character: '{char}'")

    elif cmd == "time":
        if not arg:
            notice("Usage: .hanb time <hanb_char>")
            return
        char = arg.strip()[0]
        scales = _load_scales()
        time_scales = scales.get("time", {})
        if char in time_scales:
            name, desc = time_scales[char]
            label = f'"{name}" ' if name else ""
            message(f"Time scale '{char}': {label}({desc})")
        else:
            message(f"Unknown hanb character: '{char}'")

    elif cmd == "render":
        if not arg:
            notice("Usage: .hanb render <61-char board string>")
            return
        board_str = arg.strip().strip("'\"").strip("`")
        rendered = render_board(board_str)
        for line in rendered.split("\n"):
            message(line)

    elif cmd == "logic":
        scales = _load_scales()
        logic = scales.get("logic", {})
        if not arg:
            # Show all logic operations
            lines = []
            for num in sorted(logic.keys()):
                name, desc = logic[num]
                lines.append(f"  {num:2d}: {name:16s} - {desc}")
            # Send in chunks to avoid flood
            chunk = []
            for line in lines:
                chunk.append(line)
                if len(chunk) >= 5:
                    message("16 hanb logic operations:")
                    for l in chunk:
                        message(l)
                    chunk = []
            if chunk:
                for l in chunk:
                    message(l)
        else:
            try:
                num = int(arg.strip())
                if num in logic:
                    name, desc = logic[num]
                    message(f"Logic op {num}: {name} - {desc}")
                else:
                    message(f"Unknown logic operation. Use 0-15.")
            except ValueError:
                message("Usage: .hanb logic [0-15]")

    elif cmd == "random":
        board = generate_random_board()
        rendered = render_board(board)
        for line in rendered.split("\n"):
            message(line)
        message(f"Random board: {board}")

    elif cmd == "count":
        boards = _load_boards()
        cats = get_categories()
        total = len(boards)
        message(f"Total boards: {total}")
        for cat in sorted(cats.keys()):
            message(f"  {cat}: {len(cats[cat])}")

    elif cmd == "search":
        if not arg:
            notice("Usage: .hanb search <query>")
            return
        boards = _load_boards()
        query = arg.strip().lower()
        matches = [name for name in boards if query in name.lower()]
        if matches:
            for m in matches[:10]:
                message(f"  {m}")
            if len(matches) > 10:
                message(f"  ... and {len(matches)-10} more")
        else:
            message(f"No boards matching '{arg}'")

    else:
        # Try to find a board
        full_text = text.strip()
        name, board = find_board(full_text)

        if board is None:
            # Check if it's a category listing
            cats = get_categories()
            cat_name = full_text.lower()
            if cat_name in cats:
                names = cats[cat_name]
                for n in names:
                    message(f"  {n}")
            else:
                # Search for partial matches
                matches = [k for k in boards if cat_name in k.lower()]
                if matches:
                    message(f"Did you mean one of these?")
                    for m in matches[:5]:
                        message(f"  {m}")
                else:
                    message(f"Unknown hanb board '{full_text}'. Use .hanb categories to see available boards.")
            return

        # Render the board
        cat = name.split("_")[0] if "_" in name else "?"
        bname = name.split("_", 1)[1] if "_" in name else name
        rendered = render_board(board)
        message(f"[{cat}/{bname}]")
        for line in rendered.split("\n"):
            message(line)
        message(f"Board: {board}")
