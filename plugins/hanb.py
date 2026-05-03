# -*- coding: utf-8 -*-
"""
hanb.py - hanb hexagonal board viewer plugin

hanb is a simple language for creating model universes at any scale.
Each board is a 61-cell hexagonal grid where each cell contains a character
from the set: a-z, A-Z, 0-9, - and .

Boards are stored in plugins/hanb_data.json and loaded dynamically.
"""

import os
import json
import random
import re
from cloudbot import hook

# Board templates for the two rotation styles
# Positions are hardcoded based on the hex grid layout from the hanb spec

# Edge-top rotation (flat top)
# Row offsets and cell counts: [padding, cell_count]
# The 61 cells map to positions in this layout:
EDGE_TEMPLATE = [
    "        {0}   {1}   {2}   {3}   {4}",
    "      {5}   {6}   {7}   {8}   {9}   {10}",
    "    {11}   {12}   {13}   {14}   {15}   {16}   {17}",
    "  {18}   {19}   {20}   {21}   {22}   {23}   {24}   {25}",
    "{26}   {27}   {28}   {29}   {30}   {31}   {32}   {33}   {34}",
    "  {35}   {36}   {37}   {38}   {39}   {40}   {41}   {42}",
    "    {43}   {44}   {45}   {46}   {47}   {48}   {49}",
    "      {50}   {51}   {52}   {53}   {54}   {55}",
    "        {56}   {57}   {58}   {59}   {60}",
]

# Point-top rotation
# The 61 cells in point-top layout use a different index mapping
# From the original hanb spec, the b[] array maps edge indices to point positions
# b = [e4, e3, e10, e2, e9, e17, e1, e8, e16, e25, e0, e7, e15, e24, e34,
#      e6, e14, e23, e33, e5, e13, e22, e32, e42, e12, e21, e31, e41, e11,
#      e20, e30, e40, e49, e19, e29, e39, e48, e18, e28, e38, e47, e55,
#      e27, e37, e46, e54, e26, e36, e45, e53, e60, e35, e44, e52, e59,
#      e43, e51, e58, e50, e57, e56, e61, e62, e63]
POINT_INDEX_MAP = [
    4, 3, 10, 2, 9, 17, 1, 8, 16, 25, 0, 7, 15, 24, 34,
    6, 14, 23, 33, 5, 13, 22, 32, 42, 12, 21, 31, 41, 11,
    20, 30, 40, 49, 19, 29, 39, 48, 18, 28, 38, 47, 55,
    27, 37, 46, 54, 26, 36, 45, 53, 60, 35, 44, 52, 59,
    43, 51, 58, 50, 57, 56, 61, 62, 63,
]

POINT_TEMPLATE = [
    "                    {0}",
    "               {1}         {2}",
    "          {3}         {4}         {5}",
    "     {6}         {7}         {8}         {9}",
    "{10}         {11}         {12}         {13}         {14}",
    "     {15}         {16}         {17}         {18}",
    "{19}         {20}         {21}         {22}         {23}",
    "     {24}         {25}         {26}         {27}",
    "{28}         {29}         {30}         {31}         {32}",
    "     {33}         {34}         {35}         {36}",
    "{37}         {38}         {39}         {40}         {41}",
    "     {42}         {43}         {44}         {45}",
    "{46}         {47}         {48}         {49}         {50}",
    "     {51}         {52}         {53}         {54}",
    "          {55}         {56}         {57}",
    "               {58}         {59}",
    "                    {60}",
]

# Load board data
_data_path = os.path.join(os.path.dirname(__file__), "hanb_data.json")
_boards = {}

try:
    with open(_data_path, "r", encoding="utf-8") as f:
        _raw = json.load(f)
    _boards = _raw.get("boards", {})
except (FileNotFoundError, json.JSONDecodeError):
    pass


def _pad_char(c):
    """Pad a single character to ensure consistent display width."""
    return c if c else " "


def _render_edge(board_str):
    """Render a 61-char board string in edge-top rotation."""
    chars = list(board_str.ljust(61)[:61])
    cells = [_pad_char(c) for c in chars]
    lines = []
    for row in EDGE_TEMPLATE:
        # The template uses {0}..{60} as indices
        line = row.format(*cells)
        lines.append(line)
    return "\n".join(lines)


def _render_point(board_str):
    """Render a 61-char board string in point-top rotation."""
    chars = list(board_str.ljust(61)[:61])
    # Map edge indices to point indices
    point_cells = [_pad_char(chars[POINT_INDEX_MAP[i]]) for i in range(61)]
    lines = []
    for row in POINT_TEMPLATE:
        line = row.format(*point_cells)
        lines.append(line)
    return "\n".join(lines)


def _render_board(board_str, style="edge"):
    """Render a board string in the given style."""
    if style == "point":
        return _render_point(board_str)
    return _render_edge(board_str)


def _get_board(name=None):
    """Get a board by name or pick a random one. Returns (name, data_dict) or None."""
    if not _boards:
        return None
    if name:
        key = name.lower().strip()
        # Exact match first
        if key in _boards:
            return key, _boards[key]
        # Try matching partial names
        for bname, bdata in _boards.items():
            if key in bname.lower() or bname.lower() in key:
                return bname, bdata
        return None
    # Random board
    name = random.choice(list(_boards.keys()))
    return name, _boards[name]


@hook.command("hanb", autohelp=False)
def hanb(text, notice):
    """
    .hanb [name|list|count] - Display a hanb hexagonal board.
    .hanb list - List available board names.
    .hanb count - Show number of available boards.
    .hanb <name> - Show a specific board by name.
    .hanb - Show a random board.
    """
    text = text.strip().lower()

    if text == "list":
        if not _boards:
            return "No hanb boards loaded."
        names = ", ".join(sorted(_boards.keys()))
        # Split into multiple messages if too long
        if len(names) > 400:
            parts = []
            current = "Available boards: "
            for n in sorted(_boards.keys()):
                addition = n + ", "
                if len(current) + len(addition) > 400:
                    parts.append(current.rstrip(", "))
                    current = ""
                current += addition
            parts.append(current.rstrip(", "))
            return parts
        return f"Available boards: {names}"

    if text == "count":
        return f"There are {len(_boards)} hanb boards available."

    if text:
        result = _get_board(text)
        if not result:
            return f"Board '{text}' not found. Use .hanb list to see available boards."
        name, data = result
    else:
        result = _get_board()
        if not result:
            return "No hanb boards loaded."
        name, data = result

    board_str = data.get("data", "." * 61)
    style = data.get("style", "edge")
    desc = data.get("description", "")
    display_name = data.get("name", name)

    rendered = _render_board(board_str, style)

    lines = rendered.split("\n")
    output = f"\x02[hanb]\x02 {display_name}"
    if desc:
        output += f" — {desc}"
    output += "\n" + "\n".join(lines)

    return output


@hook.command("hanbadd", autohelp=False)
def hanbadd(text, notice, chan, is_admin):
    """
    .hanbadd <name> <style> <61-char-data> [description] - Add a new hanb board (admin only).
    Style: edge or point. Data must be exactly 61 characters from a-zA-Z0-9-.
    """
    if not is_admin:
        return notice("Only admins can add hanb boards.")

    parts = text.strip().split(None, 3)
    if len(parts) < 3:
        return notice("Usage: .hanbadd <name> <style> <61-char-data> [description]")

    name = parts[0].lower().strip()
    name = re.sub(r'[^a-z0-9_]', '_', name)
    style = parts[1].lower().strip()
    data = parts[2]
    desc = parts[3] if len(parts) > 3 else ""

    if style not in ("edge", "point"):
        return notice("Style must be 'edge' or 'point'.")

    if len(data) != 61:
        return notice(f"Data must be exactly 61 characters, got {len(data)}.")

    valid_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.")
    if not all(c in valid_chars for c in data):
        return notice("Data contains invalid characters. Use only a-z, A-Z, 0-9, -, and .")

    if name in _boards:
        return notice(f"Board '{name}' already exists.")

    _boards[name] = {
        "name": name.replace("_", " ").title(),
        "data": data,
        "style": style,
        "description": desc,
    }

    # Save to file
    try:
        save_data = {"boards": _boards}
        with open(_data_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return notice(f"Failed to save: {e}")

    return f"Added hanb board '{name}' ({style} rotation). Use .hanb {name} to view it."


@hook.command("hanbdel", autohelp=False)
def hanbdel(text, notice, is_admin):
    """
    .hanbdel <name> - Remove a hanb board (admin only).
    """
    if not is_admin:
        return notice("Only admins can delete hanb boards.")

    name = text.strip().lower()
    if not name:
        return notice("Usage: .hanbdel <name>")

    if name not in _boards:
        return notice(f"Board '{name}' not found.")

    del _boards[name]

    try:
        save_data = {"boards": _boards}
        with open(_data_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return notice(f"Failed to save: {e}")

    return f"Deleted hanb board '{name}'."
