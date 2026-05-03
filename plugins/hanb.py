import os
from random import choice

from cloudbot import hook

HANBS_DIR = os.path.join(os.path.dirname(__file__), "hanbs")


def load_hanbs():
    """Dynamically load all hanb board files from the hanbs/ directory."""
    hanbs = []
    if not os.path.isdir(HANBS_DIR):
        return hanbs
    for fname in sorted(os.listdir(HANBS_DIR)):
        if fname.endswith(".txt"):
            fpath = os.path.join(HANBS_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    hanbs.append(content)
            except Exception:
                pass
    return hanbs


HANBS = load_hanbs()


@hook.command("hanb")
def hanb_command(text, notice):
    """<name|list> - Display a hanb hex board. Use 'list' to see available boards, or a name for a specific one."""
    if not HANBS:
        return "No hanbs loaded! The hanbs directory is empty."

    cmd = text.strip().lower() if text else ""

    if cmd == "list":
        names = []
        for fname in sorted(os.listdir(HANBS_DIR)):
            if fname.endswith(".txt"):
                names.append(fname[:-4])
        return "Available hanbs: " + ", ".join(names) + f" ({len(names)} total)"

    if cmd:
        # Try to find a specific hanb by name
        target_file = cmd + ".txt"
        target_path = os.path.join(HANBS_DIR, target_file)
        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                return f"Error reading hanb '{cmd}'."
        # Partial match
        matches = []
        for fname in sorted(os.listdir(HANBS_DIR)):
            if fname.endswith(".txt") and cmd in fname[:-4].lower():
                matches.append(fname[:-4])
        if len(matches) == 1:
            with open(os.path.join(HANBS_DIR, matches[0] + ".txt"), "r", encoding="utf-8") as f:
                return f.read().strip()
        elif matches:
            return f"Multiple matches: {', '.join(matches)}. Be more specific."
        else:
            return f"No hanb found matching '{cmd}'. Use .hanb list to see available boards."

    # Random hanb
    return choice(HANBS)
