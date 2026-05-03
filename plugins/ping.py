from cloudbot import hook


@hook.command()
def ping(message):
    """- Replies with pong"""
    message("pong")
