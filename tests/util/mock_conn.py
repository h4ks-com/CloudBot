from unittest.mock import MagicMock

from plugins.core import chan_track


class MockConn:
    def __init__(self, *, nick=None, name=None):
        self.nick = nick or "TestBot"
        self.name = name or "testconn"
        self.config = {}
        self.history = {}
        self.memory = {}
        self.keepalive = []
        self.reload = MagicMock()
        self.try_connect = MagicMock()
        self.notice = MagicMock()
        self.join = MagicMock()
        self.ready = True

    def is_nick_valid(self, nick):
        return True


def account_conn(nick, account):
    """A connection where ``nick`` is identified to services as ``account``, or unidentified with None."""
    conn = MockConn()
    user = chan_track.get_users(conn).getuser(nick)
    user.account = account
    # UsersDict holds users by weakref, so we keep a strong ref for as long as the test uses the connection.
    conn.keepalive.append(user)
    return conn
