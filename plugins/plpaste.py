import os
from os import listdir

from cloudbot import hook


@hook.command("ghsource", "ghpaste", "plugin", "commandsource", "cmdsrc")
def ghpaste(text, bot):
    """<command|plugin> - Returns a GitHub source URL (with exact line number) for any bot command or plugin. \
Fetch that URL to read the implementation source code. \
Also works with a bare plugin filename (no .py). \
Use this to understand how any command works before calling it."""
    repo_link = bot.config.get(
        "repo_link", "https://github.com/h4ks-com/CloudBot"
    )

    if text in bot.plugin_manager.commands:
        file_path = bot.plugin_manager.commands[text].plugin.file_path
        relative_path = os.path.relpath(file_path)

        line_number = None
        try:
            with open(file_path, encoding="utf-8") as f:
                lines = f.readlines()
                for i, line in enumerate(lines, 1):
                    if f"def {text}(" in line:
                        line_number = i
                        break
                    elif "@hook.command" in line and f'"{text}"' in line:
                        for j in range(i, len(lines)):
                            s = lines[j].strip()
                            if s.startswith("def ") or s.startswith("async def "):
                                line_number = j + 1
                                break
                        break
        except (OSError, UnicodeDecodeError):
            pass

        github_url = f"{repo_link}/blob/main/{relative_path}"
        if line_number:
            github_url += f"#L{line_number}"

        return f"Command '{text}' is defined in: {github_url}"
    elif text + ".py" in listdir("plugins/"):
        github_url = f"{repo_link}/blob/main/plugins/{text}.py"
        return f"Plugin '{text}' can be found at: {github_url}"
    else:
        return f"Command '{text}' not found."
