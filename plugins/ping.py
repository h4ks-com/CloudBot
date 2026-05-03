from cloudbot import hook


@hook.command("ping")
def ping(text, reply):
    """ping - Replies with pong."""
    reply("pong")
