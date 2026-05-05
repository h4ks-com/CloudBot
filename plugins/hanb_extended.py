from random import choice

from cloudbot import hook

# Suppress the original hanb command
_original_loaded = True


@hook.command("hanb", autohelp=False, priority=1)
def hanb(text: str):
    """- Prints a random hanb (extended)"""
    # Lazy import to avoid circular issues
    from plugins.hanb_new import NEW_HANBS

    # We need to get the original HANBS - import the module data directly
    import importlib
    import plugins.hanb as hanb_mod
    all_hanbs = list(hanb_mod.HANBS) + NEW_HANBS
    ranb = choice(all_hanbs)
    return ranb.split("\n")
