"""Recent chat messages by their IRCv3 msgid, so a reply can be resolved to the message it answers."""

from collections import OrderedDict
from dataclasses import dataclass

MAX_MESSAGES = 2000


@dataclass(frozen=True)
class ChatMessage:
    """One PRIVMSG or ACTION as plugins see it."""

    msgid: str | None
    nick: str
    target: str
    text: str
    time: float


class MessageLog:
    """The last messages of one connection by msgid, oldest dropped first."""

    def __init__(self, size: int = MAX_MESSAGES) -> None:
        self._size = size
        self._messages: OrderedDict[str, ChatMessage] = OrderedDict()

    def add(self, message: ChatMessage) -> None:
        if not message.msgid:
            return
        self._messages[message.msgid] = message
        self._messages.move_to_end(message.msgid)
        while len(self._messages) > self._size:
            self._messages.popitem(last=False)

    def get(self, msgid: str) -> ChatMessage | None:
        return self._messages.get(msgid)
