import json
import random
from pathlib import Path

from cloudbot import hook

# Valid hanb characters: a-z, A-Z, 0-9, -, .
VALID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789-."
)

# Edge rotation: 61-cell hexagonal board (flat-top)
EDGE_TEMPLATE = """        {0}   {1}   {2}   {3}   {4}
      {5}   {6}   {7}   {8}   {9}   {10}
    {11}   {12}   {13}   {14}   {15}   {16}   {17}
  {18}   {19}   {20}   {21}   {22}   {23}   {24}   {25}
{26}   {27}   {28}   {29}   {30}   {31}   {32}   {33}   {34}
  {35}   {36}   {37}   {38}   {39}   {40}   {41}   {42}
    {43}   {44}   {45}   {46}   {47}   {48}   {49}
      {50}   {51}   {52}   {53}   {54}   {55}
        {56}   {57}   {58}   {59}   {60}"""

# Point rotation cell indices for 61-cell board
# Maps position 0-60 to the pointy-top hex layout
POINT_INDICES = [
    4, 3, 10, 2, 9, 17, 1, 8, 16, 25, 0, 7, 15, 24, 34,
    6, 14, 23, 33, 5, 13, 22, 32, 42, 12, 21, 31, 41,
    11, 20, 30, 40, 49, 19, 29, 39, 48, 18, 28, 38, 47, 55,
    27, 37, 46, 54, 26, 36, 45, 53, 60, 35, 44, 52, 59,
    43, 51, 58, 50, 57, 56, 61, 62, 63,
]

POINT_TEMPLATE = """                    {0}
               {1}         {2}
          {3}         {4}         {5}
     {6}         {7}         {8}         {9}
{10}         {11}         {12}         {13}         {14}
     {15}         {16}         {17}         {18}
{19}         {20}         {21}         {22}         {23}
     {24}         {25}         {26}         {27}
{28}         {29}         {30}         {31}         {32}
     {33}         {34}         {35}         {36}
{37}         {38}         {39}         {40}         {41}
     {42}         {43}         {44}         {45}
{46}         {47}         {48}         {49}         {50}
     {51}         {52}         {53}         {54}
          {55}         {56}         {57}
               {58}         {59}
                    {60}"""

# In-memory category storage
_categories = {}  # name -> {"name": str, "description": str, "boards": list}


def _pad_board(code, length=61):
    """Pad or truncate a board code to exactly 61 characters."""
    if len(code) < length:
        code = code + "." * (length - len(code))
    return code[:length]


def _validate_board(code):
    """Check if all characters in the board code are valid hanb characters."""
    return all(c in VALID_CHARS for c in code)


def render_edge(code):
    """Render a 61-char board string in edge (flat-top) rotation."""
    padded = _pad_board(code)
    cells = [padded[i] if i < len(padded) else "." for i in range(61)]
    return EDGE_TEMPLATE.format(*cells)


def render_point(code):
    """Render a 61-char board string in point (pointy-top) rotation."""
    padded = _pad_board(code)
    base = [padded[i] if i < len(padded) else "." for i in range(61)]
    cells = [base[POINT_INDICES[i]] if POINT_INDICES[i] < 61 else "." for i in range(61)]
    return POINT_TEMPLATE.format(*cells)


def _generate_random_board():
    """Generate a random 61-character hanb board code."""
    chars = list(VALID_CHARS)
    return "".join(random.choice(chars) for _ in range(61))


def _load_category(bot, cat_name):
    """Load a single category JSON file from the hanb data directory."""
    path = bot.data_path / "hanb" / f"{cat_name}.json"
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Validate boards
        for board in data.get("boards", []):
            code = board.get("code", "")
            board["code"] = _pad_board(code)
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _get_all_categories(bot):
    """Get all available categories, loading from disk if needed."""
    hanb_dir = bot.data_path / "hanb"
    if not hanb_dir.is_dir():
        return {}

    categories = {}
    for json_file in sorted(hanb_dir.glob("*.json")):
        cat_name = json_file.stem
        data = _load_category(bot, cat_name)
        if data:
            categories[cat_name] = data
    return categories


def _get_random_board_from_categories(categories):
    """Pick a random board from all loaded categories."""
    all_boards = []
    for cat_name, cat_data in categories.items():
        for board in cat_data.get("boards", []):
            all_boards.append((cat_name, board))

    if not all_boards:
        return None, None

    cat_name, board = random.choice(all_boards)
    return cat_name, board


@hook.on_start()
def load_hanb_categories(bot):
    """Load all hanb categories from data/hanb/*.json at bot startup."""
    _categories.clear()
    categories = _get_all_categories(bot)
    _categories.update(categories)


@hook.command("hanb", autohelp=False)
async def hanb(text, bot, notice):
    """[category|list|categories] - Display a random hanb board. Use 'list' to see categories, or specify a category name."""
    text = text.strip().lower()

    if not text or text == "random":
        # Random board from all categories
        if not _categories:
            categories = _get_all_categories(bot)
            _categories.update(categories)

        if not _categories:
            return "No hanb boards loaded. Check plugins/data/hanb/ directory."

        cat_name, board = _get_random_board_from_categories(_categories)
        if not board:
            return "No hanb boards found."

        code = board.get("code", _generate_random_board())
        name = board.get("name", "Unknown")
        rendered = render_edge(code)

        return f"\x02{name}\x02 [{cat_name}] (edge)\n{rendered}"

    elif text in ("list", "categories", "cats"):
        # List all categories
        if not _categories:
            categories = _get_all_categories(bot)
            _categories.update(categories)

        if not _categories:
            return "No hanb categories loaded."

        total_boards = sum(
            len(cat.get("boards", [])) for cat in _categories.values()
        )
        cat_list = ", ".join(sorted(_categories.keys()))
        return f"Hanb categories ({total_boards} boards): {cat_list}"

    else:
        # Specific category
        if not _categories:
            categories = _get_all_categories(bot)
            _categories.update(categories)

        if text in _categories:
            cat = _categories[text]
            boards = cat.get("boards", [])
            if not boards:
                return f"Category '{text}' has no boards."

            board = random.choice(boards)
            code = board.get("code", _generate_random_board())
            name = board.get("name", "Unknown")
            rendered = render_edge(code)

            return f"\x02{name}\x02 [{text}] (edge)\n{rendered}"
        else:
            available = ", ".join(sorted(_categories.keys()))
            return f"Unknown category '{text}'. Available: {available}"


@hook.command("hanbp", autohelp=False)
async def hanbp(text, bot):
    """[category] - Display a random hanb board in point (pointy-top) rotation."""
    text = text.strip().lower()

    if not _categories:
        categories = _get_all_categories(bot)
        _categories.update(categories)

    if not _categories:
        return "No hanb boards loaded."

    if text and text in _categories:
        boards = _categories[text].get("boards", [])
        if boards:
            board = random.choice(boards)
            cat_name = text
        else:
            return f"Category '{text}' has no boards."
    else:
        cat_name, board = _get_random_board_from_categories(_categories)
        if not board:
            return "No hanb boards found."

    code = board.get("code", _generate_random_board())
    name = board.get("name", "Unknown")
    rendered = render_point(code)

    return f"\x02{name}\x02 [{cat_name}] (point)\n{rendered}"


@hook.command("hanbr", autohelp=False)
async def hanbr(text, bot):
    """<61-char code> - Render a specific hanb board code in edge rotation."""
    code = text.strip()
    if not code:
        return "Usage: .hanbr <61-char board code>"

    if not _validate_board(code):
        invalid = set(code) - VALID_CHARS
        return f"Invalid characters in board: {', '.join(sorted(invalid))}. Valid: a-z A-Z 0-9 - ."

    rendered = render_edge(code)
    return f"Hanb board ({len(code)} cells, edge):\n{rendered}"


@hook.command("hanbrp", autohelp=False)
async def hanbrp(text, bot):
    """<61-char code> - Render a specific hanb board code in point rotation."""
    code = text.strip()
    if not code:
        return "Usage: .hanbrp <61-char board code>"

    if not _validate_board(code):
        invalid = set(code) - VALID_CHARS
        return f"Invalid characters in board: {', '.join(sorted(invalid))}. Valid: a-z A-Z 0-9 - ."

    rendered = render_point(code)
    return f"Hanb board ({len(code)} cells, point):\n{rendered}"


@hook.command("hanbg", autohelp=False)
async def hanbg(text, notice):
    """- Generate a completely random hanb board."""
    code = _generate_random_board()
    rendered = render_edge(code)
    return f"\x02Random hanb board\x02\n{rendered}"
