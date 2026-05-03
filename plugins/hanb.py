import os
from random import choice

from cloudbot import hook

HANB_DIR = os.path.join(os.path.dirname(__file__), "hanbs")

HANBS = []

for filename in sorted(os.listdir(HANB_DIR)):
    if filename.endswith(".txt"):
        with open(os.path.join(HANB_DIR, filename), encoding="utf-8") as f:
            HANBS.append(f.read().strip())


@hook.command("hanb")
def hanb_command(text):
    """Display a random hexagonal board."""
    return choice(HANBS)
