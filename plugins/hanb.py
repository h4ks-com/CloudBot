import os
from random import choice

from cloudbot import hook

# Directory where hanb board files live
_HANB_DIR = os.path.join(os.path.dirname(__file__), "hanbs")


def _load_hanbs():
    """Dynamically load all .txt files from the hanbs/ directory."""
    boards = []
    if not os.path.isdir(_HANB_DIR):
        return boards
    for fname in sorted(os.listdir(_HANB_DIR)):
        if fname.lower().endswith(".txt"):
            fpath = os.path.join(_HANB_DIR, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    text = f.read().strip()
                if text:
                    boards.append(text)
            except OSError:
                pass
    return boards


HANBS = _load_hanbs()


@hook.command("hanb")
def hanb(cmd):
    """Display a random hanb board."""
    if not HANBS:
        return "No hanbs loaded! Check the hanbs/ directory."
    return choice(HANBS)
