import os
import glob
from random import choice

from cloudbot import hook

# Path to hanb data files
_HANB_DIR = os.path.join(os.path.dirname(__file__), "hanb_data")


def _load_hanbs():
    """Dynamically load all .txt files from the hanb_data directory."""
    hanbs = []
    if not os.path.isdir(_HANB_DIR):
        return hanbs
    for filepath in sorted(glob.glob(os.path.join(_HANB_DIR, "*.txt"))):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                hanbs.append(content)
        except Exception:
            pass
    return hanbs


HANBS = _load_hanbs()


@hook.command("hanb", autohelp=False)
def hanb(text: str):
    """- Prints a random hanb"""
    if not HANBS:
        return "No hanbs loaded! Check plugins/hanb_data/ directory."
    ranb = choice(HANBS)
    return ranb.split("\n")
