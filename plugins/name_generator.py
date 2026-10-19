"""
name_generator.py

Generates fun names using the textgen module.

Created By:
    - Luke Rogers <https://github.com/lukeroge>

License:
    GPL v3
"""

import json
from typing import Annotated

from cloudbot import hook
from cloudbot.bot import CloudBot
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util import formatting, textgen


def get_generator(_json):
    data = json.loads(_json)
    return textgen.TextGenerator(
        data["templates"],
        data["parts"],
        default_templates=data["default_templates"],
    )


@hook.command(autohelp=False)
def namegen(
    bot: CloudBot,
    event: CommandEvent,
    generator_name: Annotated[
        str | None, Arg("Name generator to use, or list to see them all")
    ] = None,
):
    """generates some names using the chosen generator, or lists all generators
    if 'list' is specified
    """

    # clean up the input
    inp = (generator_name or "").strip().lower()

    # get a list of available name generators
    path = bot.data_path / "name_files"
    files = path.glob("*.json")
    module_map = {file.stem: file for file in files}
    all_modules = list(module_map.keys())
    all_modules.sort()

    # command to return a list of all available generators
    if inp == "list":
        message = "Available generators: "
        message += formatting.get_text_list(all_modules, "and")
        event.notice(message)
        return None

    if inp:
        selected_module = inp.split()[0]
    else:
        # make some generic fantasy names
        selected_module = "fantasy"

    # load the name generator
    try:
        path = module_map[selected_module]
    except KeyError:
        return f"{inp} is not a valid name generator."

    with open(path, encoding="utf-8") as f:
        try:
            generator = get_generator(f.read())
        except ValueError as error:
            return f"Unable to read name file: {error}"

    # time to generate some names
    name_list = generator.generate_strings(10)

    # and finally return the final message :D
    return "Some names to ponder: {}.".format(
        formatting.get_text_list(name_list, "and")
    )
