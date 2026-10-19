from re import findall
from typing import Annotated

from cloudbot import hook
from cloudbot.clients.irc import IrcClient
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util.formatting import get_text_list

polls: dict[str, "Poll"] = {}


class PollError(Exception):
    pass


class PollOption:
    def __init__(self, title):
        self.title = title
        self.votes = 0


class Poll:
    def __init__(self, question, creator, options=("Yes", "No")):
        self.question = question
        self.creator = creator
        self.option_list = list(options)
        self.options = {i.lower(): PollOption(i) for i in options}

        self.voted = []

    def vote(self, voted_option, voter):
        """
        Adds a vote to a specific poll option. Raises PollError if option is invalid or user has already voted.
        Returns PollOption if sucessful.

        :param voted_option: The poll option to vote on
        :param voter: The user who is voting on the poll
        """
        voted_option = voted_option.lower()

        # check if the option is valid
        if voted_option not in self.options.keys():
            raise PollError("Sorry, that's not a valid option for this poll.")

        # make sure the user hasn't already voted
        if voter.lower() in self.voted:
            raise PollError("Sorry, you have already voted on this poll.")

        # fetch the option object, and increment option.votes
        option = self.options.get(voted_option)
        option.votes += 1

        self.voted.append(voter.lower())
        return option

    def format_results(self):
        # store a list of options, and sort by votes
        options = list(self.options.values())
        options.sort(key=lambda x: x.votes)

        output = []
        for o in self.options.values():
            string = f"{o.title}: {o.votes}"
            output.append(string)

        return ", ".join(output)


@hook.command()
def poll(
    conn: IrcClient,
    nick: str,
    chan: str,
    event: CommandEvent,
    question: Annotated[
        str,
        Arg("Question, then : and comma separated options, or close"),
    ],
):
    """Creates a poll for <question> with the provided options (default: Yes, No), or closes the poll if the argument is 'close'"""
    # get poll ID
    uid = ":".join([conn.name, chan, nick]).lower()

    if question.lower() == "close":
        if uid not in polls.keys():
            return "You have no active poll to close."

        p = polls.get(uid)
        event.reply(
            f'Your poll has been closed. Final results for \x02"{p.question}"\x02:'
        )
        event.message(p.format_results())
        del polls[uid]
        return None

    if uid in polls.keys():
        return "You already have an active poll in this channel, you must close it before you can create a new one."

    if ":" in question:
        question, options = question.split(":")
        c = findall(r"([^,]+)", options)
        if len(c) == 1:
            c = findall(r"(\S+)", options)

        options = []
        for o in c:
            o = o.strip()
            if o not in options:
                options.append(o)

        _poll = Poll(question, nick, options)
    else:
        _poll = Poll(question, nick)

    # store poll in list
    polls[uid] = _poll

    option_str = get_text_list(_poll.option_list, "and")
    event.message(
        f'Created poll \x02"{_poll.question}"\x02 with the following options: {option_str}'
    )
    event.message(f"Use .vote {nick.lower()} <option> to vote on this poll!")
    return None


@hook.command(autohelp=True)
def vote(
    nick: str,
    conn: IrcClient,
    chan: str,
    event: CommandEvent,
    user: Annotated[str, Arg("Nick whose poll to vote on", kind="user")],
    option: Annotated[str, Arg("Option to vote for")],
):
    """Vote on a poll; responds on error and silently records on success."""
    uid = ":".join([conn.name, chan, user]).lower()

    p = polls.get(uid)

    if p is None:
        return "Sorry, there is no active poll from that user."

    try:
        o = p.vote(option, nick)
    except PollError as e:
        return str(e)

    event.notice(f'Voted \x02"{o.title}"\x02 on {p.creator}\'s poll!')
    return None


@hook.command(autohelp=False)
def results(
    conn: IrcClient,
    chan: str,
    nick: str,
    event: CommandEvent,
    user: Annotated[
        str | None, Arg("Nick whose poll to show", kind="user")
    ] = None,
):
    """Shows current results from [user]'s poll. If [user] is empty, it will show results for your poll."""
    if user:
        uid = ":".join([conn.name, chan, user]).lower()
        if uid not in polls.keys():
            return "Sorry, there is no active poll from that user."
    else:
        uid = ":".join([conn.name, chan, nick]).lower()
        if uid not in polls.keys():
            return "You have no current poll. Use .vote <user> <option> to vote on another users poll."

    p = polls.get(uid)

    event.reply(f'Results for \x02"{p.question}"\x02 by \x02{p.creator}\x02:')
    event.message(p.format_results())
    return None
