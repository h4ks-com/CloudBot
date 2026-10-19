import datetime
import re
from unittest.mock import MagicMock, call, patch

import pytest

from cloudbot.util.command_args import CommandArgError, build_spec
from plugins import duckhunt
from tests.util.mock_conn import MockConn


def test_duck_kick_args():
    spec = build_spec(duckhunt.no_duck_kick)
    assert spec.parse_text("Enable") == {"mode": "enable"}
    with pytest.raises(CommandArgError):
        spec.parse_text("foo")
    with pytest.raises(CommandArgError):
        spec.parse_text("enable now")


@pytest.mark.parametrize(
    "prefix,items,result",
    [
        [
            "Duck friend scores in #TestChannel: ",
            {
                "testuser": 5,
                "testuser1": 1,
            },
            "Duck friend scores in #TestChannel: "
            "\x02t\u200bestuser\x02: 5 • \x02t\u200bestuser1\x02: 1",
        ],
    ],
)
def test_top_list(prefix, items, result, mock_db):
    assert duckhunt.top_list(prefix, items.items()) == result


def test_update_score(mock_db):
    duckhunt.table.create(mock_db.engine)
    mock_db.add_row(
        duckhunt.table,
        network="net",
        chan="#chan",
        name="nick",
        shot=1,
        befriend=3,
    )
    session = mock_db.session()

    conn = MockConn(name="net")
    chan = "#chan"
    res = duckhunt.update_score("nick", chan, session, conn, shoot=1)
    assert res == {"shoot": 2, "friend": 3}
    assert mock_db.get_data(duckhunt.table) == [("net", "nick", 2, 3, "#chan")]


def test_display_scores(mock_db):
    duckhunt.table.create(mock_db.engine)

    session = mock_db.session()

    conn = MockConn()

    chan = "#TestChannel"
    chan1 = "#TestChannel1"

    duckhunt.update_score("TestUser", chan, session, conn, 5, 4)
    duckhunt.update_score("TestUser1", chan, session, conn, 1, 7)
    duckhunt.update_score("OtherUser", chan1, session, conn, 9, 2)

    expected_testchan_friend_scores = {"testuser": 4, "testuser1": 7}

    actual_testchan_friend_scores = duckhunt.get_channel_scores(
        session, duckhunt.SCORE_TYPES["friend"], conn, chan
    )

    assert actual_testchan_friend_scores == expected_testchan_friend_scores

    chan_friends = (
        "Duck friend scores in #TestChannel: "
        "\x02t\u200bestuser1\x02: 7 • \x02t\u200bestuser\x02: 4"
    )

    chan_kills = (
        "Duck killer scores in #TestChannel: "
        "\x02t\u200bestuser\x02: 5 • \x02t\u200bestuser1\x02: 1"
    )

    global_friends = (
        "Duck friend scores across the network: "
        "\x02t\u200bestuser1\x02: 7"
        " • \x02t\u200bestuser\x02: 4"
        " • \x02o\u200btheruser\x02: 2"
    )

    global_kills = (
        "Duck killer scores across the network: "
        "\x02o\u200btheruser\x02: 9"
        " • \x02t\u200bestuser\x02: 5"
        " • \x02t\u200bestuser1\x02: 1"
    )

    average_friends = (
        "Duck friend scores across the network: "
        "\x02t\u200bestuser1\x02: 7"
        " • \x02t\u200bestuser\x02: 4"
        " • \x02o\u200btheruser\x02: 2"
    )

    average_kills = (
        "Duck killer scores across the network: "
        "\x02o\u200btheruser\x02: 9"
        " • \x02t\u200bestuser\x02: 5"
        " • \x02t\u200bestuser1\x02: 1"
    )

    assert duckhunt.friends(chan, conn, session) == chan_friends

    assert duckhunt.killers(chan, conn, session) == chan_kills

    assert duckhunt.friends(chan, conn, session, "global") == global_friends

    assert duckhunt.killers(chan, conn, session, "global") == global_kills

    assert duckhunt.friends(chan, conn, session, "average") == average_friends

    assert duckhunt.killers(chan, conn, session, "average") == average_kills

    spec = build_spec(duckhunt.killers)
    assert spec.parse_text("GLOBAL") == {"scope": "global"}
    assert spec.parse_text("") == {"scope": None}
    with pytest.raises(CommandArgError):
        spec.parse_text("#channel")


def test_ignore_integration(mock_db):
    event = MagicMock()
    event.chan = "#chan"
    event.mask = "nick!user@host"
    event.host = "host"

    ignore_plugin = MagicMock()

    ignore_plugin.code.is_ignored.return_value = True

    plugins = {
        "ignore": ignore_plugin,
    }

    def find_plugin(name):
        return plugins.get(name)

    event.bot.plugin_manager.find_plugin = find_plugin

    conn = event.conn
    conn.name = "testconn"

    tbl = duckhunt.get_state_table(conn.name, event.chan)
    tbl.game_on = True
    tbl.duck_status = 0

    duckhunt.increment_msg_counter(event, conn)

    assert not tbl.masks

    ignore_plugin.code.is_ignored.return_value = False

    duckhunt.increment_msg_counter(event, conn)

    assert tbl.masks == [event.host]


def test_no_duck_kick_opt_out(mock_db):
    duckhunt.status_table.create(mock_db.engine)
    with patch.object(duckhunt, "is_opt_out") as mocked:
        mocked.return_value = True
        db = mock_db.session()
        conn = MockConn()
        res = duckhunt.no_duck_kick(db, "#foo", conn, mode="enable")
        assert res is None
        assert mock_db.get_data(duckhunt.status_table) == []


def test_stop_hunt_opt_out(mock_db):
    duckhunt.status_table.create(mock_db.engine)
    with patch.object(duckhunt, "is_opt_out") as mocked:
        mocked.return_value = True
        db = mock_db.session()
        conn = MockConn()
        chan = "#foo"
        res = duckhunt.stop_hunt(db, chan, conn)
        assert res is None
        assert mock_db.get_data(duckhunt.status_table) == []


def test_start_hunt_opt_out(mock_db):
    duckhunt.status_table.create(mock_db.engine)
    with patch.object(duckhunt, "is_opt_out") as mocked:
        mocked.return_value = True
        db = mock_db.session()
        conn = MockConn()
        event = MagicMock()
        chan = "#foo"
        res = duckhunt.start_hunt(db, chan, event, conn)
        assert res is None
        assert event.mock_calls == []
        assert mock_db.get_data(duckhunt.status_table) == []


def test_start_hunt(mock_db):
    duckhunt.status_table.create(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    event = MagicMock()
    chan = "#foo"
    res = duckhunt.start_hunt(db, chan, event, conn)
    assert res is None
    assert event.mock_calls == [
        call.message(
            "Ducks have been spotted nearby. See how many you can shoot or save. use .bang to shoot or .befriend to save them. NOTE: Ducks now appear as a function of time and channel activity.",
            "#foo",
        )
    ]
    assert mock_db.get_data(duckhunt.status_table) == [
        ("testconn", "#foo", True, False)
    ]


def test_duck_migrate_no_data(mock_db):
    duckhunt.table.create(mock_db.engine)
    conn = MockConn()
    event = MagicMock()
    db = mock_db.session()
    res = duckhunt.duck_merge(conn, db, event, "a", "b")
    assert res == "There are no duck scores to migrate from a"
    assert event.mock_calls == []
    assert mock_db.get_data(duckhunt.table) == []


def test_duck_migrate(mock_db):
    duckhunt.table.create(mock_db.engine)
    conn = MockConn()
    conn.name = "foo"
    mock_db.add_row(
        duckhunt.table,
        network=conn.name,
        name="bar",
        shot=12,
        befriend=10,
        chan="#test",
    )

    mock_db.add_row(
        duckhunt.table,
        network=conn.name,
        name="bar",
        shot=9,
        befriend=8,
        chan="#test1",
    )

    mock_db.add_row(
        duckhunt.table,
        network=conn.name,
        name="other",
        shot=3,
        befriend=0,
        chan="#test1",
    )

    event = MagicMock()
    db = mock_db.session()
    res = duckhunt.duck_merge(conn, db, event, "bar", "other")

    assert event.mock_calls == [
        call.message(
            "Migrated 21 duck kills and 18 duck friends from bar to other"
        )
    ]
    assert mock_db.get_data(duckhunt.table) == [
        ("foo", "other", 12, 8, "#test1"),
        ("foo", "other", 12, 10, "#test"),
    ]
    assert res is None


def test_duck_stats_user_single_chan(mock_db):
    duckhunt.table.create(mock_db.engine)
    chan = "#foo"
    nick = "foobar"
    conn = MockConn()
    mock_db.add_row(
        duckhunt.table,
        network=conn.name,
        name=nick,
        shot=20,
        befriend=18,
        chan=chan.lower(),
    )
    event = MagicMock()
    db = mock_db.session()
    res = duckhunt.ducks_user(nick, chan, conn, db, event)
    assert res is None
    assert event.mock_calls == [
        call.message(
            "foobar has killed 20 ducks and befriended 18 ducks in #foo."
        )
    ]


def test_duck_stats_no_data(mock_db):
    duckhunt.table.create(mock_db.engine)
    conn = MockConn()
    event = MagicMock()
    chan = "#foo"
    db = mock_db.session()
    res = duckhunt.duck_stats(chan, conn, db, event)
    assert (
        res
        == "It looks like there has been no duck activity on this channel or network."
    )
    assert event.mock_calls == []


class TestOptOut:
    def test_opt_out(self, mock_db):
        duckhunt.optout.create(mock_db.engine)
        mock_db.add_row(duckhunt.optout, network="net", chan="#chan")
        duckhunt.load_optout(mock_db.session())
        assert duckhunt.is_opt_out("net", "#chan")
        assert not duckhunt.is_opt_out("net2", "#chan")

    def test_set_opt_out_on(self, mock_db):
        duckhunt.table.create(mock_db.engine)
        duckhunt.optout.create(mock_db.engine)
        duckhunt.status_table.create(mock_db.engine)

        duckhunt.load_optout(mock_db.session())
        duckhunt.load_status(mock_db.session())

        conn = MockConn(name="net")
        res = duckhunt.hunt_opt_out(
            "#chan", mock_db.session(), conn, "add", "#chan"
        )
        assert res == "The duckhunt has been successfully disabled in #chan."
        assert mock_db.get_data(duckhunt.optout) == [("net", "#chan")]

    def test_set_opt_out_off(self, mock_db):
        duckhunt.table.create(mock_db.engine)
        duckhunt.optout.create(mock_db.engine)
        duckhunt.status_table.create(mock_db.engine)
        mock_db.add_row(duckhunt.optout, chan="#chan", network="net")

        duckhunt.load_optout(mock_db.session())
        duckhunt.load_status(mock_db.session())

        conn = MockConn(name="net")
        assert duckhunt.is_opt_out("net", "#chan")
        res = duckhunt.hunt_opt_out(
            "#chan", mock_db.session(), conn, "remove", "#chan"
        )
        assert res is None
        assert mock_db.get_data(duckhunt.optout) == []


class TestStatus:
    def test_load(self, mock_db):
        duckhunt.game_status.clear()
        duckhunt.status_table.create(mock_db.engine)
        mock_db.add_row(
            duckhunt.status_table,
            network="net",
            chan="#chan",
            active=True,
            duck_kick=True,
        )
        duckhunt.load_status(mock_db.session())
        state = duckhunt.get_state_table("net", "#chan")
        assert state.game_on
        assert state.no_duck_kick

    def test_save_and_load(self, mock_db):
        duckhunt.game_status.clear()
        duckhunt.status_table.create(mock_db.engine)
        duckhunt.load_status(mock_db.session())
        conn = MockConn(name="net")
        duckhunt.set_game_state(mock_db.session(), conn, "#foo", active=True)
        state = duckhunt.get_state_table("net", "#foo")
        assert state.game_on
        assert not state.no_duck_kick

    def test_save_all(self, mock_db):
        duckhunt.game_status.clear()
        duckhunt.status_table.create(mock_db.engine)
        duckhunt.load_status(mock_db.session())
        duckhunt.game_status["net"]["#chan"] = state = duckhunt.ChannelState()
        state.next_duck_time = 7
        state.no_duck_kick = True
        duckhunt.save_status(mock_db.session(), _sleep=False)
        assert mock_db.get_data(duckhunt.status_table) == [
            ("net", "#chan", False, True),
        ]

    def test_save_on_exit(self, mock_db):
        duckhunt.game_status.clear()
        duckhunt.status_table.create(mock_db.engine)
        duckhunt.load_status(mock_db.session())
        duckhunt.game_status["net"]["#chan"] = state = duckhunt.ChannelState()
        state.next_duck_time = 7
        state.no_duck_kick = True
        duckhunt.save_on_exit(mock_db.session())
        assert mock_db.get_data(duckhunt.status_table) == [
            ("net", "#chan", False, True),
        ]


class TestStartHunt:
    def test_start_hunt(self, mock_db):
        duckhunt.optout.create(mock_db.engine)
        duckhunt.load_optout(mock_db.session())
        duckhunt.game_status.clear()
        duckhunt.status_table.create(mock_db.engine)
        duckhunt.load_status(mock_db.session())
        conn = MockConn(name="net")
        event = MagicMock()
        message = event.message
        res = duckhunt.start_hunt(mock_db.session(), "#chan", event, conn)
        assert mock_db.get_data(duckhunt.status_table) == [
            ("net", "#chan", True, False)
        ]
        assert message.mock_calls == [
            call(
                "Ducks have been spotted nearby. See how many you can shoot or save. use .bang to shoot or .befriend to save them. NOTE: Ducks now appear as a function of time and channel activity.",
                "#chan",
            )
        ]
        assert res is None


class TestStopHunt:
    def test_stop_hunt(self, mock_db):
        duckhunt.optout.create(mock_db.engine)
        duckhunt.load_optout(mock_db.session())
        duckhunt.game_status.clear()
        duckhunt.status_table.create(mock_db.engine)
        mock_db.add_row(
            duckhunt.status_table,
            network="net",
            chan="#chan",
            active=True,
            duck_kick=False,
        )
        duckhunt.load_status(mock_db.session())
        conn = MockConn(name="net")
        res = duckhunt.stop_hunt(mock_db.session(), "#chan", conn)
        assert mock_db.get_data(duckhunt.status_table) == [
            ("net", "#chan", False, False)
        ]
        assert res == "the game has been stopped."


class TestDuckKick:
    def test_enable_duck_kick(self, mock_db):
        duckhunt.optout.create(mock_db.engine)
        duckhunt.load_optout(mock_db.session())
        duckhunt.game_status.clear()
        duckhunt.status_table.create(mock_db.engine)
        mock_db.add_row(
            duckhunt.status_table,
            network="net",
            chan="#chan",
            active=True,
            duck_kick=False,
        )
        duckhunt.load_status(mock_db.session())
        conn = MockConn(name="net")
        res = duckhunt.no_duck_kick(
            mock_db.session(), "#chan", conn, mode="enable"
        )
        assert mock_db.get_data(duckhunt.status_table) == [
            ("net", "#chan", True, True)
        ]
        assert (
            res
            == "users will now be kicked for shooting or befriending non-existent ducks. The bot needs to have appropriate flags to be able to kick users for this to work."
        )

    def test_disable_duck_kick(self, mock_db):
        duckhunt.optout.create(mock_db.engine)
        duckhunt.load_optout(mock_db.session())
        duckhunt.game_status.clear()
        duckhunt.status_table.create(mock_db.engine)
        mock_db.add_row(
            duckhunt.status_table,
            network="net",
            chan="#chan",
            active=True,
            duck_kick=True,
        )
        duckhunt.load_status(mock_db.session())
        conn = MockConn(name="net")
        res = duckhunt.no_duck_kick(
            mock_db.session(), "#chan", conn, mode="disable"
        )
        assert mock_db.get_data(duckhunt.status_table) == [
            ("net", "#chan", True, False)
        ]
        assert res == "kicking for non-existent ducks has been disabled."


class TestAttack:
    def test_shoot(self, mock_db, freeze_time):
        duckhunt.table.create(mock_db.engine)
        duckhunt.optout.create(mock_db.engine)
        duckhunt.status_table.create(mock_db.engine)

        mock_db.add_row(
            duckhunt.status_table, network="net", chan="#chan", active=True
        )

        duckhunt.load_optout(mock_db.session())
        duckhunt.load_status(mock_db.session())

        state = duckhunt.get_state_table("net", "#chan")
        state.duck_status = 1
        state.duck_time = datetime.datetime.now().timestamp() - 3600.0

        conn = MockConn(name="net")
        event = MagicMock()
        with patch.object(duckhunt, "hit_or_miss") as p:
            p.return_value = 0
            res = duckhunt.bang(
                re.match(r"^\s*(.+)bang\s*$", ".bang"),
                "nick",
                "#chan",
                mock_db.session(),
                conn,
                event,
            )

        assert res is None
        assert event.mock_calls == [
            call.message(
                "nick you shot a duck in 1 hour! You have killed 1 duck in #chan."
            )
        ]
        assert mock_db.get_data(duckhunt.table) == [
            ("net", "nick", 1, 0, "#chan")
        ]

    def test_befriend(self, mock_db, freeze_time):
        duckhunt.table.create(mock_db.engine)
        duckhunt.optout.create(mock_db.engine)
        duckhunt.status_table.create(mock_db.engine)

        mock_db.add_row(
            duckhunt.status_table, network="net", chan="#chan", active=True
        )

        duckhunt.load_optout(mock_db.session())
        duckhunt.load_status(mock_db.session())

        state = duckhunt.get_state_table("net", "#chan")
        state.duck_status = 1
        state.duck_time = datetime.datetime.now().timestamp() - 3600.0

        conn = MockConn(name="net")
        event = MagicMock()
        with patch.object(duckhunt, "hit_or_miss") as p:
            p.return_value = 1
            res = duckhunt.befriend(
                "nick", "#chan", mock_db.session(), conn, event
            )

        assert res is None
        assert event.mock_calls == [
            call.message(
                "nick you befriended a duck in 1 hour! You have made friends with 1 duck in #chan."
            )
        ]
        assert mock_db.get_data(duckhunt.table) == [
            ("net", "nick", 0, 1, "#chan")
        ]


@pytest.mark.parametrize(
    "func,text,expected",
    [
        (duckhunt.hunt_opt_out, "", {"action": None, "channel": None}),
        (duckhunt.hunt_opt_out, "list", {"action": "list", "channel": None}),
        (
            duckhunt.hunt_opt_out,
            "ADD #chan",
            {"action": "add", "channel": "#chan"},
        ),
        (duckhunt.duck_merge, "a b", {"user1": "a", "user2": "b"}),
        (duckhunt.ducks_user, "", {"target": None}),
        (duckhunt.ducks_user, "bob", {"target": "bob"}),
        (duckhunt.duckforgive, "bob", {"nick": "bob"}),
    ],
)
def test_typed_args_parse(func, text, expected):
    assert build_spec(func).parse_text(text) == expected


def test_typed_args_reject():
    with pytest.raises(CommandArgError):
        build_spec(duckhunt.duck_merge).parse_text("a")
    with pytest.raises(CommandArgError):
        build_spec(duckhunt.hunt_opt_out).parse_text("nope #chan")
