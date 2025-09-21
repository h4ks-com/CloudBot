import os
from os import listdir

from cloudbot import hook


@hook.command("ghsource", "ghpaste", "plugin", "commandsource", "cmdsrc")
def ghpaste(text, bot):
    """<command> - links to the GitHub file that contains <command>"""
    repo_link = bot.config.get(
        "repo_link", "https://github.com/h4ks-com/CloudBot"
    )

    if text in bot.plugin_manager.commands:
        file_path = bot.plugin_manager.commands[text].plugin.file_path
        # Convert absolute path to relative path from project root
        relative_path = os.path.relpath(file_path)

        # Find the line number where the function is defined
        line_number = None
        try:
            with open(file_path, encoding="utf-8") as f:
                lines = f.readlines()
                for i, line in enumerate(lines, 1):
                    # Check for function definition
                    if f"def {text}(" in line:
                        line_number = i
                        break
                    # Check for @hook.command decorator with command name
                    elif "@hook.command" in line and f'"{text}"' in line:
                        # Find the next function definition
                        for j in range(i, len(lines)):
                            if lines[j].strip().startswith("def "):
                                line_number = j + 1
                                break
                        break
        except (OSError, UnicodeDecodeError):
            pass

        github_url = f"{repo_link}/blob/master/{relative_path}"
        if line_number:
            github_url += f"#L{line_number}"

        return f"Command '{text}' is defined in: {github_url}"
    elif text + ".py" in listdir("plugins/"):
        github_url = f"{repo_link}/blob/master/plugins/{text}.py"
        return f"Plugin '{text}' can be found at: {github_url}"
    else:
        return "Could not find specified plugin."
