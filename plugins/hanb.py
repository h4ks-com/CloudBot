import json
import os
from random import choice

from cloudbot import hook

# --- Inline hanbs (original) ---
HANBS = [
    """
🍁🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🍁🍁🍁
🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🍁🍁
🍁🍁🌿🍁🌿🍁🌿🍁📜🍁🌿🍁🌿🍁🌿🍁🍁
🍁🌿🍁🌿🍁🌿🍁📜🍁📜🍁🌿🍁🌿🍁🌿🍁
🌿🍁🌿🍁🌿🍁🌿🍁🤵‍♂️🍁🧤🍁🌿🍁🌿🍁🌿
🍁🌿🍁🌿🍁🧤🍁📜🍁📜🍁🌿🍁🌿🍁🌿🍁
🍁🍁🌿🍁🌿🍁🌿🍁📜🍁📜🍁🌿🍁🌿🍁🍁
🍁🍁🍁🌿🍁🌿🍁📜🍁📜🍁🌿🍁🌿🍁🍁🍁
🍁🍁🍁🍁🌿🍁🥾🍁📜🍁🥾🍁🌿🍁🍁🍁🍁
""",
    """
🍁🍁🍁🍁🌿🍁🌿🍁⏸🍁🌿🍁🌿🍁🍁🍁🍁
🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🍁🍁
🍁🍁🌿🍁🌿🍁🌿🍁⏸🍁🌿🍁🌿🍁🌿🍁🍁
🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁
🌿🍁🌿🍁🌿🍁🌿🍁⏸🍁🌿🍁🌿🍁🌿🍁🌿
🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁
🍁🍁🌿🍁🌿🍁💠🍁🔴🍁💠🍁🌿🍁🌿🍁🍁
🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🍁🍁
🍁🍁🍁🍁🌿🍁🌿🍁💎🍁🌿🍁🌿🍁🍁🍁🍁
""",
    """
🍁🍁🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁💠🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🔸🍁🔸🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🌀🍁🌀🍁🌀🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🌿🍁🌿🍁🌀🍁🌿🍁🌿🍁🌀🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🌿🍁🌿🍁🌀🍁🌿🍁🌿🍁🌿🍁🌀🍁🌿🍁🌿🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🌿🍁🌿🍁🌀🍁🌿🍁🌿🍁🌀🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🌀🍁🌿🍁🌀🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🌀🍁🌀🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁🍁🍁🍁
""",
    """
▗▗▗▗▗▗▗▗a.a.a.a.a.▗▗▗▗▗▗▗▗
▗▗▗▗▗▗▗K.a.a.a.a.a.▗▗▗▗▗▗▗
▗▗▗▗▗▗K.K.a.a.K.K.a.▗▗▗▗▗▗
▗▗▗▗▗a.a.a.a.K.a.a.a.▗▗▗▗▗
▗▗▗▗a.a.a.a.K.a.a.a.J.▗▗▗▗
▗▗▗▗▗a.a.a.a.a.a.a.a.▗▗▗▗▗
▗▗▗▗▗▗a.a.a.J.a.a.a.▗▗▗▗▗▗
▗▗▗▗▗▗▗a.J.a.a.J.a.▗▗▗▗▗▗▗
▗▗▗▗▗▗▗▗a.a.a.a.a.▗▗▗▗▗▗▗▗
""",
    """
🍁🍁🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🌿🍁🏘️🍁🌿🍁🏘️🍁🌿🍁🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🏘️🍁🌿🍁🌿🍁🌿🍁🌿🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🏘️🍁🌿🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🏠🍁🌿🍁🌿🍁🌿🍁🏠🍁🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🌿🍁🏘️🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁🍁🍁
🍁🍁🍁🍁🍁🍁🍁🍁🌿🍁🌿🍁🌿🍁🌿🍁🌿🍁🍁🍁🍁🍁🍁🍁🍁🍁
""",
    """
✨✨✨✨✨✨✨✨🌌✨🌟✨🌟✨🌌✨🌌✨✨✨✨✨✨✨✨✨
✨✨✨✨✨✨✨🌌✨🌟✨🌌✨🌌✨🌟✨🌟✨✨✨✨✨✨✨✨
✨✨✨✨✨✨🌌✨🌟✨🌌✨🌟✨🌟✨🌌✨🌌✨✨✨✨✨✨✨
✨✨✨✨✨🌌✨🌌✨🌟✨🌟✨🌟✨🌌✨🌟✨🌌✨✨✨✨✨✨
✨✨✨✨🌌✨🌟✨🌌✨🌟✨🌟✨🌟✨🌟✨🌌✨🌌✨✨✨✨✨
✨✨✨✨✨🌌✨🌟✨🌟✨🌟✨🌟✨🌟✨🌌✨🌌✨✨✨✨✨✨
✨✨✨✨✨✨🌌✨🌌✨🌌✨🌌✨🌌✨🌟✨🌌✨✨✨✨✨✨✨
✨✨✨✨✨✨✨🌌✨🌌✨🌌✨🌟✨🌌✨🌌✨✨✨✨✨✨✨✨
✨✨✨✨✨✨✨✨🌌✨🌟✨🌌✨🌌✨🌌✨✨✨✨✨✨✨✨✨
""",
    """
🥛🥛🥛🥛🥛🥛🥛🥛✨🥛✨🥛✨🥛✨🥛✨🥛🥛🥛🥛🥛🥛🥛🥛🥛
🥛🥛🥛🥛🥛🥛🥛✨🥛✨🥛✨🥛✨🥛✨🥛✨🥛🥛🥛🥛🥛🥛🥛🥛
🥛🥛🥛🥛🥛🥛✨🥛✨🥛✨🥛✨🥛✨🥛✨🥛✨🥛🥛🥛🥛🥛🥛🥛
🥛🥛🥛🥛🥛✨🥛✨🥛✨🥛🎁🥛🎁🥛✨🥛✨🥛✨🥛🥛🥛🥛🥛🥛🥛
🥛🥛🥛🥛✨🥛🎁🥛✨🥛🎁🥛🎁🥛🎁🥛✨🥛🎁🥛✨🥛🥛🥛🥛🥛🥛
🥛🥛🥛🥛🥛✨🥛✨🥛✨🥛🎁🥛🎁🥛✨🥛✨🥛✨🥛🥛🥛🥛🥛🥛🥛
🥛🥛🥛🥛🥛🥛✨🥛✨🥛✨🥛✨🥛✨🥛✨🥛✨🥛🥛🥛🥛🥛🥛🥛
🥛🥛🥛🥛🥛🥛🥛✨🥛✨🥛✨🥛✨🥛✨🥛✨🥛🥛🥛🥛🥛🥛🥛🥛
🥛🥛🥛🥛🥛🥛🥛🥛✨🥛✨🥛🎁🥛✨🥛✨🥛🥛🥛🥛🥛🥛🥛🥛🥛
""",
    """
🌾🌾🌾🌾🌾🌾🌾🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌾🌾🌾🌾🌾🌾🌾🌾
🌾🌾🌾🌾🌾🌾🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌾🌾🌾🌾🌾🌾🌾
🌾🌾🌾🌾🌾🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌾🌾🌾🌾🌾🌾
🌾🌾🌾🌾🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌾🌾🌾🌾🌾🌾
🌾🌾🌾🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌾🌾🌾🌾🌾
🌾🌾🌾🌾🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌾🌾🌾🌾🌾🌾
🌾🌾🌾🌾🌾🌾🌺🌾🌺🌾🌺🌾🌺🌾🌺🌾🌾🌾🌾🌾🌾🌾🌾
🌾🌾🌾🌾🌾🌾🌾🌺🌾🌺🌾🌺🌾🌺🌾🌾🌾🌾🌾🌾🌾🌾🌾
🌾🌾🌾🌾🌾🌾🌾🌾🌺🌾🌺🌾🌺🌾🌾🌾🌾🌾🌾🌾🌾🌾🌾
""",
    """
🌲🌲🌲🌲🌲🌲🌲🌲❄️🌲❄️🌲❄️🌲❄️🌲❄️🌲🌲🌲🌲🌲🌲🌲🌲🌲
🌲🌲🌲🌲🌲🌲🌲❄️🌲❄️🌲🐈🌲❄️🌲❄️🌲❄️🌲🌲🌲🌲🌲🌲🌲🌲
🌲🌲🌲🌲🌲🌲❄️🌲🕳️🌲❄️🌲❄️🌲❤️🌲🐈🌲❄️🌲🌲🌲🌲🌲🌲🌲
🌲🌲🌲🌲🌲❄️🌲❄️🌲❄️🌲❄️🌲❤️🌲❄️🌲⛳🌲❄️🌲🌲🌲🌲🌲🌲
🌲🌲🌲🌲❄️🌲❄️🌲🐈🌲❄️🌲❄️🌲🐈🌲❄️🌲❄️🌲❄️🌲🌲🌲🌲🌲
🌲🌲🌲🌲🌲❄️🌲❄️🌲❄️🌲❄️🌲❄️🌲🕳️🌲🐈🌲❄️🌲🌲🌲🌲🌲🌲
🌲🌲🌲🌲🌲🌲❄️🌲❤️🌲⛳🌲❄️🌲❄️🌲❄️🌲❄️🌲🌲🌲🌲🌲🌲🌲
🌲🌲🌲🌲🌲🌲🌲❄️🌲❄️🌲🐈🌲❄️🌲❄️🌲❄️🌲🌲🌲🌲🌲🌲🌲🌲
🌲🌲🌲🌲🌲🌲🌲🌲❄️🌲❄️🌲❄️🌲❄️🌲❄️🌲🌲🌲🌲🌲🌲🌲🌲🌲
""",
    """
🌊🌊🌊🌊🌊🌊🌊🌊🏝️🌊🏝️🌊🏝️🌊⛵🌊🏝️🌊🌊🌊🌊🌊🌊🌊🌊🌊
🌊🌊🌊🌊🌊🌊🌊🏝️🌊🏝️🌊🐟🌊🏝️🌊🐳🌊🐳🌊🌊🌊🌊🌊🌊🌊🌊
🌊🌊🌊🌊🌊🌊🏝️🌊⛵🌊🏝️🌊🐳🌊🏝️🌊🐳🌊🏝️🌊🌊🌊🌊🌊🌊🌊
🌊🌊🌊🌊🌊🏝️🌊🐟🌊🐟🌊⛵🌊🐟🌊⛵🌊🏝️🌊🏝️🌊🌊🌊🌊🌊🌊
🌊🌊🌊🌊🏝️🌊🏝️🌊🐟🌊🏝️🌊🏝️🌊🏝️🌊🐟🌊🏝️🌊🏝️🌊🌊🌊🌊🌊
🌊🌊🌊🌊🌊🏝️🌊🏝️🌊🏝️🌊🏝️🌊🏝️🌊🏝️🌊⛵🌊🏝️🌊🌊🌊🌊🌊🌊
🌊🌊🌊🌊🌊🌊🏝️🌊⛵🌊🐟🌊⛵🌊🏝️🌊🐟🌊🏝️🌊🌊🌊🌊🌊🌊🌊
🌊🌊🌊🌊🌊🌊🌊🏝️🌊🏝️🌊🏝️🌊🏝️🌊🏝️🌊🏝️🌊🌊🌊🌊🌊🌊🌊🌊
🌊🌊🌊🌊🌊🌊🌊🌊🏝️🌊🏝️🌊🏝️🌊🏝️🏝️🌊🌊🌊🌊🌊🌊🌊🌊🌊
""",
    """
✨✨✨✨✨✨✨✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨✨✨✨✨✨✨✨✨
✨✨✨✨✨✨✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨✨✨✨✨✨✨✨
✨✨✨✨✨✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨✨✨✨✨✨✨
✨✨✨✨✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨✨✨✨✨✨✨
✨✨✨✨🇺🇸✨🇺🇸✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨🇨🇦✨✨✨✨✨
✨✨✨✨✨🇺🇸✨🇺🇸✨🇨🇦✨🇺🇸✨🇺🇸✨🇨🇦✨🇨🇦✨🇺🇸✨✨✨✨✨✨
✨✨✨✨✨✨🇺🇸✨🇺🇸✨🇺🇸✨🇺🇸✨🇺🇸✨🇺🇸✨🇺🇸✨✨✨✨✨✨✨
✨✨✨✨✨✨✨🇺🇸✨🇺🇸✨🇺🇸✨🇺🇸✨🇺🇸✨🇺🇸✨✨✨✨✨✨✨✨
✨✨✨✨✨✨✨✨🇺🇸✨🇺🇸✨🇺🇸✨🇺🇸✨🇺🇸✨✨✨✨✨✨✨✨✨
""",
    """
🌺🌺🌺🌺🌺🌺🌺🌺⛄🌺🏔️🌺🏔️🌺🏔️🌺🏔️🌺🌺🌺🌺🌺🌺🌺🌺🌺
🌺🌺🌺🌺🌺🌺🌺⛄🌺⛄🌺🌲🎅❄️🌺🏔️🌺🏔️🌺🌺🌺🌺🌺🌺🌺🌺
🌺🌺🌺🌺🌺🌺⛄🌺⛄🌺🏔️🌺❄️🌺❄️🌺🏔️🌺🏔️🌺🌺🌺🌺🌺🌺🌺
🌺🌺🌺🌺🌺⛄🌺⛄🌺🏔️🌺🌲🌺❄️🎅❄️🌺🏔️🌺🏔️🌺🌺🌺🌺🌺🌺
🌺🌺🌺🌺⛄🌺⛄🌺🏔️🎅❄️🌺❄️🌺❄️🌺❄️🌺🏔️🌺🏔️🌺🌺🌺🌺🌺
🌺🌺🌺🌺🌺⛄🌺⛄🌺❄️🌺❄️🌺❄️🌺❄️🌺❄️🌺🏔️🌺🌺🌺🌺🌺🌺
🌺🌺🌺🌺🌺🌺⛄🌺⛄🌺❄️🎅❄️🌺❄️🌺🏔️🌺🏔️🌺🌺🌺🌺🌺🌺🌺
🌺🌺🌺🌺🌺🌺🌺⛄🌺⛄🌺❄️🌺❄️🌺❄️🌺🏔️🌺🌺🌺🌺🌺🌺🌺🌺
🌺🌺🌺🌺🌺🌺🌺🌺⛄🌺⛄🌺❄️🌺❄️🌺🏔️🌺🌺🌺🌺🌺🌺🌺🌺🌺
""",
    """
🚧🚧🚧🚧🚧🚧🚧🚧🚗🚦🛢🚦🛢🚦🛢🚦🛢🚧🚧🚧🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚧🚧🚗🚦🚗🚦🛣️🚦🚑🚦🛢🚦🛢🚧🚧🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚧🚗🚦🚗🚦🏔️🚦🚕🚦🚕🚦🛢🚦🛢🚧🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚗🚦🚗🚦🏔️🚦🛣️🚦🚓🚦🚕🚦🛢🚦🛢🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚗🚦🚗🚦🛢🚦🚕🚦❄️🚦🛢🚦❄️🚦🛢🚦🛢🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚗🚦🚗🚦🚓🚦🚕🚦🚕🚦🚕🚦🚕🚦🛢🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚧🚗🚦🚗🚦🚕🚦🚕🚦🚕🚦🛢🚦🛢🚧🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚧🚧🚗🚦🚗🚦🚕🚦🚑🚦🚕🚦🛢🚧🚧🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚧🚧🚧🚗🚦🚗🚦🚕🚦🚕🚦🛢🚧🚧🚧🚧🚧🚧🚧🚧🚧
""",
    """
🚧🚧🚧🚧🚧🚧🚧🚧🗾🏠🗾🏠🗾🏠🗾🏠🗾🚧🚧🚧🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚧🚧🗾🏠🗾🏠🗾🏠🗾🏠🗾🏠🗾🚧🚧🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚧🗾🏠🏠🏠🗻🏨🗾🏠🗾🏠⛪🗾🚧🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🗾🏠🏡🗾🏡🗾🏫🗾🏠🗾🏠🏚️🗾🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🗾🏠🏡🗾🏡🗾🏠🗾🏠🏚️🗾🏚️🗾🏚️🗾🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🗾🏠🏡🗻🏡🗻🏠🏠🗾🏚️🗾🏚️🗾🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚧🗾⛪🏡🏠🏠🗾🏠🏠🗺️🏚️🗾🚧🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚧🚧🗾🏠🏠🏠🏠🏨🏚️🗾🚧🚧🚧🚧🚧🚧🚧🚧
🚧🚧🚧🚧🚧🚧🚧🚧🗾🏠🏠🏠🏠🗾🚧🚧🚧🚧🚧🚧🚧🚧🚧
""",
]

# --- Dynamic hanb loading from data folder ---
_DATA_DIR = os.path.join(os.path.dirname(__file__), "hanb_data")
_HANB_CATEGORIES = {}


def _load_hanb_data():
    """Load all hanb JSON files from the hanb_data directory."""
    if not os.path.isdir(_DATA_DIR):
        return
    for filename in sorted(os.listdir(_DATA_DIR)):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(_DATA_DIR, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            category = data.get("category", filename[:-5])
            hanbs = data.get("hanbs", [])
            display_name = data.get("display_name", category)
            _HANB_CATEGORIES[category] = {
                "display_name": display_name,
                "hanbs": hanbs,
            }
        except (json.JSONDecodeError, OSError):
            continue


_load_hanb_data()


def _get_all_hanbs():
    """Return all hanbs: inline + dynamically loaded."""
    all_hanbs = list(HANBS)
    for cat_data in _HANB_CATEGORIES.values():
        all_hanbs.extend(cat_data["hanbs"])
    return all_hanbs


def _get_category_hanbs(category):
    """Return hanbs from a specific category."""
    cat_data = _HANB_CATEGORIES.get(category)
    if cat_data:
        return cat_data["hanbs"]
    return []


@hook.command("hanb")
def hanb(text):
    """Display a random hanb hexagonal board art. Usage: .hanb [category]"""
    text = text.strip().lower()
    if text:
        # Check if a category was specified
        category_hanbs = _get_category_hanbs(text)
        if category_hanbs:
            return choice(category_hanbs)
        # Maybe it's a partial match
        matches = [cat for cat in _HANB_CATEGORIES if text in cat]
        if len(matches) == 1:
            return choice(_HANB_CATEGORIES[matches[0]]["hanbs"])
        elif matches:
            return f"Multiple categories match: {', '.join(matches)}. Use .hanbcategories to see all."
        return f"Unknown category '{text}'. Use .hanbcategories to see available categories."

    return choice(_get_all_hanbs())


@hook.command("hanbcategories")
def hanb_categories(text):
    """List all available hanb categories."""
    if not _HANB_CATEGORIES:
        return "No categories loaded. The hanb_data directory may be missing."

    total_inline = len(HANBS)
    total_dynamic = sum(len(v["hanbs"]) for v in _HANB_CATEGORIES.values())
    total = total_inline + total_dynamic

    parts = [f"Available hanb categories ({total} total: {total_inline} inline + {total_dynamic} dynamic):"]
    for cat, data in sorted(_HANB_CATEGORIES.items()):
        count = len(data["hanbs"])
        parts.append(f"  {data['display_name']} ({cat}) - {count} hanb(s)")
    parts.append("Use .hanb <category> for a specific category, or .hanb for random.")
    return "\n".join(parts)
