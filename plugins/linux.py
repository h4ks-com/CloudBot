import re

from cloudbot import hook
from cloudbot.util.web import get_session


@hook.command(autohelp=False)
def kernel(reply):
    """- gets a list of linux kernel versions"""
    r = get_session().get("https://www.kernel.org/finger_banner")
    r.raise_for_status()
    contents = r.text
    contents = re.sub(r"The latest(\s*)", "", contents)
    contents = re.sub(r"version of the Linux kernel is:(\s*)", "- ", contents)
    lines = contents.split("\n")

    message = "Linux kernel versions: {}".format(
        ", ".join(line for line in lines[:-1])
    )
    reply(message)
