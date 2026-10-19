"""
ping.py

Generates fun names using the textgen module.

Created By:
    - Bjorn Neergaard <https://github.com/neersighted>

Modified By:
    - Luke Rogers <https://github.com/lukeroge>

License:
    GPL v3
"""

import os
import re
import subprocess
from typing import Annotated

from cloudbot import hook
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg

unix_ping_regex = re.compile(r"(\d+.\d+)/(\d+.\d+)/(\d+.\d+)/(\d+.\d+)")
win_ping_regex = re.compile(
    r"Minimum = (\d+)ms, Maximum = (\d+)ms, Average = (\d+)ms"
)

IS_WINDOWS = os.name == "nt"


@hook.command()
def ping(
    event: CommandEvent,
    host: Annotated[str, Arg("Host to ping")],
    count: Annotated[int, Arg("How many pings, at most 20")] = 5,
):
    """pings <host> [count] times"""
    count = min(count, 20)

    if IS_WINDOWS:
        args = ["ping", "-n", str(count), host]
    else:
        args = ["ping", "-c", str(count), host]

    event.reply(f"Attempting to ping {host} {count} times...")
    try:
        pingcmd = subprocess.check_output(args).decode("utf-8")
    except subprocess.CalledProcessError:
        return "Could not ping host."

    if re.search("(?:not find host|timed out|unknown host)", pingcmd, re.I):
        return "Could not ping host."

    if IS_WINDOWS:
        m = re.search(win_ping_regex, pingcmd)
        r = int(m.group(2)) - int(m.group(1))
        return f"min: {m.group(1)}ms, max: {m.group(2)}ms, average: {m.group(3)}ms, range: {r}ms, count: {count}"

    m = re.search(unix_ping_regex, pingcmd)
    return f"min: {m.group(1)}ms, max: {m.group(3)}ms, average: {m.group(2)}ms, range: {m.group(4)}ms, count: {count}"
