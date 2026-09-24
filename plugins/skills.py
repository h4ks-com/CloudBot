from cloudbot import hook


@hook.command(autohelp=False)
def skills(bot):
    """- returns the github url for the skills folder"""
    repo_link = bot.config.get(
        "repo_link", "https://github.com/h4ks-com/CloudBot"
    )
    return f"{repo_link}/tree/main/skills"
