import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from plugins import duel
from tests.util.mock_conn import MockConn


@pytest.fixture(autouse=True)
def _reset_duel_state():
    duel.pending.clear()
    duel.duel_games.clear()
    duel.duel_opponents.clear()
    duel.too_late_to_bang.clear()
    duel.opt_out.clear()
    duel.game_status.clear()
    duel.chan_locks.clear()
    duel.scripters.clear()
    yield
    duel.pending.clear()
    duel.duel_games.clear()
    duel.duel_opponents.clear()
    duel.too_late_to_bang.clear()
    duel.opt_out.clear()
    duel.game_status.clear()
    duel.chan_locks.clear()
    duel.scripters.clear()


def make_game(*, open=True, canceled=False) -> duel.DuelGame:
    return duel.DuelGame(
        chan="#chan",
        nicks=["bob", "alice"],
        open=open,
        start_time=None,
        canceled=canceled,
    )


def enable_game(conn, chan):
    duel.get_state_table(conn.name, chan).game_on = True


def create_tables(engine):
    duel.table.create(engine, checkfirst=True)
    duel.optout.create(engine, checkfirst=True)
    duel.status_table.create(engine, checkfirst=True)


# --- opt out / status persistence ---


def test_load_optout_and_is_opt_out(mock_db):
    create_tables(mock_db.engine)
    mock_db.add_row(duel.optout, network="net", chan="#a")
    db = mock_db.session()
    duel.load_optout(db)
    assert duel.is_opt_out("net", "#A") is True
    assert duel.is_opt_out("net", "#b") is False
    assert duel.is_opt_out("other", "#a") is False


def test_load_status_populates_state(mock_db):
    create_tables(mock_db.engine)
    mock_db.add_row(
        duel.status_table, network="net", chan="#a", active=True, duel_kick=True
    )
    db = mock_db.session()
    duel.load_status(db)
    state = duel.get_state_table("net", "#a")
    assert state.game_on is True
    assert state.no_duel_kick is True


def test_save_channel_state_insert_then_update(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    duel.save_channel_state(
        db, "net", "#a", SimpleNamespace(game_on=True, no_duel_kick=False)
    )
    assert mock_db.get_data(duel.status_table) == [("net", "#a", True, False)]
    duel.save_channel_state(
        db, "net", "#a", SimpleNamespace(game_on=False, no_duel_kick=True)
    )
    assert mock_db.get_data(duel.status_table) == [("net", "#a", False, True)]


def test_set_game_state(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    duel.set_game_state(db, conn, "#a", active=True)
    assert duel.get_state_table("net", "#a").game_on is True
    assert mock_db.get_data(duel.status_table) == [("net", "#a", True, False)]


def test_save_status_no_sleep(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    duel.get_state_table("net", "#a").game_on = True
    duel.get_state_table("net", "#b").game_on = False
    duel.save_status(db, _sleep=False)
    rows = {
        (r[0], r[1]): (r[2], r[3]) for r in mock_db.get_data(duel.status_table)
    }
    assert rows[("net", "#a")] == (True, False)
    assert rows[("net", "#b")] == (False, False)


def test_save_on_exit(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    duel.get_state_table("net", "#a").game_on = True
    duel.save_on_exit(db)
    assert mock_db.get_data(duel.status_table) == [("net", "#a", True, False)]


def test_get_config_from_conn():
    conn = SimpleNamespace(
        config={"plugins": {"duelhunt": {"foo": "conn-value"}}},
        bot=SimpleNamespace(config={}),
    )
    assert duel.get_config(conn, "foo", "default") == "conn-value"


def test_get_config_from_bot():
    conn = SimpleNamespace(
        config={},
        bot=SimpleNamespace(
            config={"plugins": {"duelhunt": {"foo": "bot-value"}}}
        ),
    )
    assert duel.get_config(conn, "foo", "default") == "bot-value"


def test_get_config_default():
    conn = SimpleNamespace(config={}, bot=SimpleNamespace(config={}))
    assert duel.get_config(conn, "foo", "default") == "default"


# --- start_duel / stop_hunt ---


def test_start_duel_opt_out(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    duel.opt_out[conn.name.casefold()].append("#a")
    result = duel.start_duel(db, "#a", MagicMock(), conn)
    assert "OPT OUT" in result


def test_start_duel_rejects_pm(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    result = duel.start_duel(db, "bob", MagicMock(), conn)
    assert "No dueling by yourself" in result


def test_start_duel_already_enabled(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    enable_game(conn, "#a")
    result = duel.start_duel(db, "#a", MagicMock(), conn)
    assert result == "Duels are already enabled in #a."


def test_start_duel_success(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    message = MagicMock()
    result = duel.start_duel(db, "#a", message, conn)
    assert result is None
    assert duel.get_state_table(conn.name, "#a").game_on is True
    message.assert_called_once()
    assert message.call_args.args[1] == "#a"


def test_stop_hunt_opt_out(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    duel.opt_out[conn.name.casefold()].append("#a")
    assert duel.stop_hunt(db, "#a", conn) is None


def test_stop_hunt_not_running(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    assert duel.stop_hunt(db, "#a", conn) == "There is no game running in #a."


def test_stop_hunt_success(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    enable_game(conn, "#a")
    result = duel.stop_hunt(db, "#a", conn)
    assert result == "the game has been stopped."
    assert duel.get_state_table(conn.name, "#a").game_on is False


# --- hit_or_miss ---


def test_hit_or_miss_too_early():
    assert duel.hit_or_miss(10, 10.5) == 0.05


def test_hit_or_miss_normal_window():
    with patch("plugins.duel.random.uniform", return_value=0.7):
        assert duel.hit_or_miss(0, 3) == 0.7


def test_hit_or_miss_too_late():
    assert duel.hit_or_miss(0, 10) == 1


# --- db helpers ---


def test_dbadd_and_update_score_new(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    result = duel.update_score("bob", "#a", db, conn, shoot=1)
    assert result == {"shoot": 1}
    assert mock_db.get_data(duel.table) == [("net", "bob", 1, "#a")]


def test_update_score_existing(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    duel.update_score("bob", "#a", db, conn, shoot=1)
    result = duel.update_score("bob", "#a", db, conn, shoot=2)
    assert result == {"shoot": 3}
    assert mock_db.get_data(duel.table) == [("net", "bob", 3, "#a")]


def test_dbupdate_requires_values(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    duel.dbadd_entry("bob", "#a", db, conn, 1)
    with pytest.raises(ValueError):
        duel.dbupdate("bob", "#a", db, conn, 0)


# --- top_list ---


def test_top_list():
    result = duel.top_list("Top Foods: ", [("Spam", 1), ("Eggs", 4)])
    assert result == "Top Foods: \x02E\u200bggs\x02: 4 • \x02S\u200bpam\x02: 1"


# --- score aggregation ---


def _load_scores(mock_db, rows):
    create_tables(mock_db.engine)
    mock_db.load_data(duel.table, rows)
    return mock_db.session()


def test_get_channel_scores(mock_db):
    db = _load_scores(
        mock_db,
        [
            {"network": "net", "chan": "#a", "name": "bob", "shot": 3},
            {"network": "net", "chan": "#a", "name": "alice", "shot": 5},
            {"network": "net", "chan": "#a", "name": "carl", "shot": 0},
            {"network": "net", "chan": "#b", "name": "bob", "shot": 1},
        ],
    )
    conn = SimpleNamespace(name="net")
    scores = duel.get_channel_scores(db, duel.SCORE_TYPES["killer"], conn, "#a")
    assert scores == {"bob": 3, "alice": 5}


def test_get_channel_scores_empty(mock_db):
    db = _load_scores(mock_db, [])
    conn = SimpleNamespace(name="net")
    assert (
        duel.get_channel_scores(db, duel.SCORE_TYPES["killer"], conn, "#a")
        is None
    )


def test_get_global_scores(mock_db):
    db = _load_scores(
        mock_db,
        [
            {"network": "net", "chan": "#a", "name": "bob", "shot": 3},
            {"network": "net", "chan": "#b", "name": "bob", "shot": 2},
        ],
    )
    conn = SimpleNamespace(name="net")
    assert duel.get_global_scores(db, duel.SCORE_TYPES["killer"], conn) == {
        "bob": 5
    }


def test_get_average_scores(mock_db):
    db = _load_scores(
        mock_db,
        [
            {"network": "net", "chan": "#a", "name": "bob", "shot": 4},
            {"network": "net", "chan": "#b", "name": "bob", "shot": 2},
        ],
    )
    conn = SimpleNamespace(name="net")
    assert duel.get_average_scores(db, duel.SCORE_TYPES["killer"], conn) == {
        "bob": 3
    }


def test_get_average_scores_empty(mock_db):
    db = _load_scores(mock_db, [])
    conn = SimpleNamespace(name="net")
    assert duel.get_average_scores(db, duel.SCORE_TYPES["killer"], conn) is None


def test_display_scores_opt_out(mock_db):
    db = _load_scores(mock_db, [])
    conn = SimpleNamespace(name="net")
    duel.opt_out["net"].append("#a")
    result = duel.display_scores(
        duel.SCORE_TYPES["killer"], MagicMock(), "", "#a", conn, db
    )
    assert result is None


def test_display_scores_no_data(mock_db):
    db = _load_scores(mock_db, [])
    conn = SimpleNamespace(name="net")
    result = duel.display_scores(
        duel.SCORE_TYPES["killer"], MagicMock(), "", "#a", conn, db
    )
    assert result == "It appears no one has killed any duels yet."


def test_display_scores_channel(mock_db):
    db = _load_scores(
        mock_db,
        [{"network": "net", "chan": "#a", "name": "bob", "shot": 5}],
    )
    conn = SimpleNamespace(name="net")
    result = duel.display_scores(
        duel.SCORE_TYPES["killer"], MagicMock(), "", "#a", conn, db
    )
    assert "duel killer scores in #a" in result
    assert "bob" in result.replace("\u200b", "")


def test_display_scores_global(mock_db):
    db = _load_scores(
        mock_db,
        [{"network": "net", "chan": "#a", "name": "bob", "shot": 5}],
    )
    conn = SimpleNamespace(name="net")
    result = duel.display_scores(
        duel.SCORE_TYPES["killer"], MagicMock(), "global", "#a", conn, db
    )
    assert "across the network" in result


def test_display_scores_average(mock_db):
    db = _load_scores(
        mock_db,
        [
            {"network": "net", "chan": "#a", "name": "bob", "shot": 4},
            {"network": "net", "chan": "#b", "name": "bob", "shot": 2},
        ],
    )
    conn = SimpleNamespace(name="net")
    result = duel.display_scores(
        duel.SCORE_TYPES["killer"], MagicMock(), "average", "#a", conn, db
    )
    assert "3" in result


def test_display_scores_invalid_key(mock_db):
    db = _load_scores(mock_db, [])
    conn = SimpleNamespace(name="net")
    event = MagicMock()
    result = duel.display_scores(
        duel.SCORE_TYPES["killer"], event, "bogus", "#a", conn, db
    )
    assert result is None
    event.notice_doc.assert_called_once()


def test_killers_command(mock_db):
    db = _load_scores(
        mock_db,
        [{"network": "net", "chan": "#a", "name": "bob", "shot": 5}],
    )
    conn = SimpleNamespace(name="net")
    result = duel.killers("", MagicMock(), "#a", conn, db)
    assert "bob" in result.replace("\u200b", "")


# --- hunt_opt_out ---


def test_hunt_opt_out_status_enabled(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    result = duel.hunt_opt_out("", "#a", db, conn)
    assert "duel is enabled in #a" in result


def test_hunt_opt_out_status_disabled(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    duel.opt_out[conn.name.casefold()].append("#a")
    result = duel.hunt_opt_out("", "#a", db, conn)
    assert "duel is disabled in #a" in result


def test_hunt_opt_out_list(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    duel.opt_out["neta"] = ["#a"]
    duel.opt_out["netb"] = ["#b"]
    result = duel.hunt_opt_out("list", "#a", db, conn)
    assert "neta" in result
    assert "netb" in result


def test_hunt_opt_out_missing_arg(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    result = duel.hunt_opt_out("add", "#a", db, conn)
    assert "please specify add or remove" in result


def test_hunt_opt_out_invalid_channel(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    result = duel.hunt_opt_out("add notachannel", "#a", db, conn)
    assert result == "Please specify a valid channel."


def test_hunt_opt_out_add(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    result = duel.hunt_opt_out("add #a", "#a", db, conn)
    assert result == "The duelhunt has been successfully disabled in #a."
    assert duel.is_opt_out(conn.name, "#a") is True
    assert mock_db.get_data(duel.optout) == [(conn.name, "#a")]


def test_hunt_opt_out_add_already_disabled(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    duel.hunt_opt_out("add #a", "#a", db, conn)
    result = duel.hunt_opt_out("add #a", "#a", db, conn)
    assert result == "duel has already been disabled in #a."


def test_hunt_opt_out_remove(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    duel.hunt_opt_out("add #a", "#a", db, conn)
    result = duel.hunt_opt_out("remove #a", "#a", db, conn)
    assert result is None
    assert duel.is_opt_out(conn.name, "#a") is False
    assert mock_db.get_data(duel.optout) == []


def test_hunt_opt_out_remove_not_disabled(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = MockConn()
    result = duel.hunt_opt_out("remove #a", "#a", db, conn)
    assert result == "duel is already enabled in #a."


# --- duel_merge ---


def test_duel_merge_no_overlap(mock_db):
    create_tables(mock_db.engine)
    mock_db.load_data(
        duel.table,
        [{"network": "net", "chan": "#a", "name": "oldnick", "shot": 5}],
    )
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    message = MagicMock()
    result = duel.duel_merge("oldnick newnick", conn, db, message)
    assert result is None
    assert mock_db.get_data(duel.table) == [("net", "newnick", 5, "#a")]
    message.assert_called_once()
    assert "Migrated" in message.call_args.args[0]


def test_duel_merge_with_overlap(mock_db):
    create_tables(mock_db.engine)
    mock_db.load_data(
        duel.table,
        [
            {"network": "net", "chan": "#a", "name": "oldnick", "shot": 5},
            {"network": "net", "chan": "#a", "name": "newnick", "shot": 2},
        ],
    )
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    message = MagicMock()
    duel.duel_merge("oldnick newnick", conn, db, message)
    data = {(r[1], r[3]): r[2] for r in mock_db.get_data(duel.table)}
    assert data[("newnick", "#a")] == 7


def test_duel_merge_no_old_scores(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    result = duel.duel_merge("oldnick newnick", conn, db, MagicMock())
    assert "no duel scores to migrate" in result


# --- duels_user / duel_stats ---


def test_duels_user_no_scores(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    result = duel.duels_user("", "bob", "#a", conn, db, MagicMock())
    assert "has not participated" in result


def test_duels_user_single_channel(mock_db):
    create_tables(mock_db.engine)
    mock_db.load_data(
        duel.table, [{"network": "net", "chan": "#a", "name": "bob", "shot": 4}]
    )
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    message = MagicMock()
    result = duel.duels_user("", "bob", "#a", conn, db, message)
    assert result is None
    message.assert_called_once_with("bob has killed 4 people in #a.")


def test_duels_user_multi_channel(mock_db):
    create_tables(mock_db.engine)
    mock_db.load_data(
        duel.table,
        [
            {"network": "net", "chan": "#a", "name": "bob", "shot": 4},
            {"network": "net", "chan": "#b", "name": "bob", "shot": 2},
        ],
    )
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    message = MagicMock()
    result = duel.duels_user("bob", "someone", "#a", conn, db, message)
    assert result is None
    message.assert_called_once()
    text = message.call_args.args[0]
    assert "bob" in text
    assert "duel stats" in text


def test_duel_stats_no_duels(mock_db):
    create_tables(mock_db.engine)
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    result = duel.duel_stats("#a", conn, db, MagicMock())
    assert "no duels" in result


def test_duel_stats_with_duels(mock_db):
    create_tables(mock_db.engine)
    mock_db.load_data(
        duel.table,
        [
            {"network": "net", "chan": "#a", "name": "bob", "shot": 4},
            {"network": "net", "chan": "#b", "name": "alice", "shot": 9},
        ],
    )
    db = mock_db.session()
    conn = SimpleNamespace(name="net")
    message = MagicMock()
    result = duel.duel_stats("#a", conn, db, message)
    assert result is None
    text = message.call_args.args[0]
    assert "#b" in text
    assert "13" in text


# --- duel_tuple / clean_duel ---


def test_duel_tuple_order_independent():
    assert duel.duel_tuple("Bob", "alice") == duel.duel_tuple("Alice", "BOB")


def test_clean_duel():
    duel.duel_opponents["bob"] = "alice"
    duel.duel_opponents["alice"] = "bob"
    duel.duel_games[duel.duel_tuple("bob", "alice")] = make_game(open=False)
    assert duel.clean_duel("bob", "alice") is True
    assert "bob" not in duel.duel_opponents
    assert "alice" not in duel.duel_opponents
    assert duel.duel_tuple("bob", "alice") not in duel.duel_games


def test_clean_duel_nothing_to_clean():
    assert duel.clean_duel("bob", "alice") is False


# --- duel() command ---


def test_duel_command_game_disabled():
    conn = MockConn()
    result = duel.duel("alice", "bob", "#a", MagicMock(), conn, MagicMock())
    assert result == "Dueling is not currently enabled in #a."


def test_duel_command_empty_text():
    conn = MockConn()
    enable_game(conn, "#a")
    result = duel.duel("", "bob", "#a", MagicMock(), conn, MagicMock())
    assert result == "Please specify a user to duel with."


def test_duel_command_invalid_nick():
    conn = MockConn()
    enable_game(conn, "#a")
    event = MagicMock()
    event.is_nick_valid.return_value = False
    result = duel.duel("al!ce", "bob", "#a", MagicMock(), conn, event)
    assert result == "That nickname is impossible to use!"


def test_duel_command_self():
    conn = MockConn()
    enable_game(conn, "#a")
    event = MagicMock()
    event.is_nick_valid.return_value = True
    result = duel.duel("bob", "bob", "#a", MagicMock(), conn, event)
    assert result == "You can't duel yourself."


def test_duel_command_already_pending():
    conn = MockConn()
    enable_game(conn, "#a")
    event = MagicMock()
    event.is_nick_valid.return_value = True
    duel.pending["alice"] = {"#a": "carl"}
    result = duel.duel("alice", "bob", "#a", MagicMock(), conn, event)
    assert "already has a pending duel request here with carl" in result


def test_duel_command_already_in_duel():
    conn = MockConn()
    enable_game(conn, "#a")
    event = MagicMock()
    event.is_nick_valid.return_value = True
    duel.duel_games[duel.duel_tuple("bob", "alice")] = make_game()
    result = duel.duel("alice", "bob", "#a", MagicMock(), conn, event)
    assert "already in a duel with you" in result


def test_duel_command_success():
    conn = MockConn()
    enable_game(conn, "#a")
    event = MagicMock()
    event.is_nick_valid.return_value = True
    duel.too_late_to_bang[("#a", "bob")] = {"start_time": None, "result": "win"}
    result = duel.duel("alice", "bob", "#a", MagicMock(), conn, event)
    assert (
        result
        == "<alice> bob has challenged you to a duel! Use .accept to accept it"
    )
    assert duel.pending["alice"]["#a"] == "bob"
    assert ("#a", "bob") not in duel.too_late_to_bang


# --- accept_duel ---


def test_accept_duel_game_disabled():
    conn = MockConn()
    result = duel.accept_duel("bob", "#a", MagicMock(), conn)
    assert result == "Dueling is not currently enabled in #a."


def test_accept_duel_no_pending():
    conn = MockConn()
    enable_game(conn, "#a")
    result = duel.accept_duel("bob", "#a", MagicMock(), conn)
    assert result == "You have no pending duels."


def test_accept_duel_no_pending_in_chan():
    conn = MockConn()
    enable_game(conn, "#a")
    duel.pending["bob"] = {"#b": "alice"}
    result = duel.accept_duel("bob", "#a", MagicMock(), conn)
    assert result == "You have no pending duels in this channel."


def test_accept_duel_success():
    conn = MockConn()
    enable_game(conn, "#a")
    duel.pending["bob"] = {"#a": "alice"}
    message = MagicMock()
    with patch("plugins.duel.Timer") as timer_cls:
        timer_instance = MagicMock()
        timer_cls.return_value = timer_instance
        with patch("plugins.duel.random.randrange", return_value=5):
            result = duel.accept_duel("bob", "#a", message, conn)
    assert result is None
    assert duel.duel_opponents["bob"] == "alice"
    assert duel.duel_opponents["alice"] == "bob"
    game = duel.duel_games[duel.duel_tuple("bob", "alice")]
    assert game["open"] is False
    assert game["start_time"] is None
    message.assert_called_once()
    assert "8 seconds" in message.call_args.args[0]
    timer_cls.assert_called_once_with(
        5, duel.duel_start_countdown, [3, "bob", "alice", "#a", message, conn]
    )
    timer_instance.start.assert_called_once()


# --- cancel_duel ---


def test_cancel_duel_game_disabled():
    conn = MockConn()
    result = duel.cancel_duel("alice", "bob", "#a", MagicMock(), conn)
    assert result == "Dueling is not currently enabled in #a."


def test_cancel_duel_no_text():
    conn = MockConn()
    enable_game(conn, "#a")
    result = duel.cancel_duel("", "bob", "#a", MagicMock(), conn)
    assert result == "Please specify a user to cancel the duel with."


def test_cancel_duel_no_pending():
    conn = MockConn()
    enable_game(conn, "#a")
    result = duel.cancel_duel("alice", "bob", "#a", MagicMock(), conn)
    assert result == "You have no pending duels with that user."


def test_cancel_duel_pending_request():
    conn = MockConn()
    enable_game(conn, "#a")
    duel.pending["alice"] = {"#a": "bob"}
    result = duel.cancel_duel("alice", "bob", "#a", MagicMock(), conn)
    assert result == "<bob> alice duel has been canceled."
    assert "#a" not in duel.pending.get("alice", {})


def test_cancel_duel_active_duel():
    conn = MockConn()
    enable_game(conn, "#a")
    duel.duel_opponents["bob"] = "alice"
    duel.duel_opponents["alice"] = "bob"
    duel.duel_games[duel.duel_tuple("bob", "alice")] = make_game(canceled=False)
    result = duel.cancel_duel("alice", "bob", "#a", MagicMock(), conn)
    assert result == "<bob> alice duel has been canceled."
    assert duel.duel_games[duel.duel_tuple("bob", "alice")]["canceled"] is True


# --- duel_start_countdown / duel_start ---


def test_duel_start_countdown_canceled():
    duel.duel_opponents["bob"] = "alice"
    duel.duel_opponents["alice"] = "bob"
    duel.duel_games[duel.duel_tuple("bob", "alice")] = make_game(canceled=True)
    message = MagicMock()
    with patch("plugins.duel.Timer") as timer_cls:
        duel.duel_start_countdown(3, "bob", "alice", "#a", message, MockConn())
    timer_cls.assert_not_called()
    message.assert_not_called()
    assert duel.duel_tuple("bob", "alice") not in duel.duel_games


def test_duel_start_countdown_recurses():
    duel.duel_games[duel.duel_tuple("bob", "alice")] = make_game(canceled=False)
    message = MagicMock()
    conn = MockConn()
    with patch("plugins.duel.Timer") as timer_cls:
        timer_instance = MagicMock()
        timer_cls.return_value = timer_instance
        duel.duel_start_countdown(2, "bob", "alice", "#a", message, conn)
    message.assert_called_once_with("<alice bob> 2")
    timer_cls.assert_called_once_with(
        1, duel.duel_start_countdown, [1, "bob", "alice", "#a", message, conn]
    )
    timer_instance.start.assert_called_once()


def test_duel_start_countdown_zero_starts_duel():
    duel.duel_games[duel.duel_tuple("bob", "alice")] = make_game(canceled=False)
    message = MagicMock()
    with patch("plugins.duel.duel_start") as start_mock:
        duel.duel_start_countdown(0, "bob", "alice", "#a", message, MockConn())
    start_mock.assert_called_once_with(
        "bob", "alice", "#a", message, start_mock.call_args.args[4]
    )


def test_duel_start_canceled():
    duel.duel_opponents["bob"] = "alice"
    duel.duel_opponents["alice"] = "bob"
    duel.duel_games[duel.duel_tuple("bob", "alice")] = make_game(canceled=True)
    message = MagicMock()
    duel.duel_start("bob", "alice", "#a", message, MockConn())
    message.assert_not_called()
    assert duel.duel_tuple("bob", "alice") not in duel.duel_games


def test_duel_start_opens_game():
    duel.duel_games[duel.duel_tuple("bob", "alice")] = make_game(canceled=False)
    message = MagicMock()
    duel.duel_start("bob", "alice", "#a", message, MockConn())
    game = duel.duel_games[duel.duel_tuple("bob", "alice")]
    assert game["open"] is True
    assert game["start_time"] is not None
    message.assert_called_once_with("<alice bob> SHOOT!")


# --- attack() / bang() ---


def make_ready_duel(conn, chan, nick, nick2, *, open_=True, start_time=None):
    duel.duel_opponents[nick.casefold()] = nick2.casefold()
    duel.duel_opponents[nick2.casefold()] = nick.casefold()
    duel.duel_games[duel.duel_tuple(nick, nick2)] = {
        "chan": chan,
        "nicks": [nick, nick2],
        "open": open_,
        "start_time": start_time if start_time is not None else duel.time(),
        "canceled": False,
    }
    enable_game(conn, chan)


def test_attack_opt_out():
    conn = MockConn()
    duel.opt_out[conn.name.casefold()].append("#a")
    result = duel.attack(
        MagicMock(), "bob", "#a", MagicMock(), conn, "shoot", "alice"
    )
    assert result is None


def test_attack_already_won():
    duel.too_late_to_bang[("#a", "bob")] = {
        "start_time": duel.time(),
        "result": "win",
    }
    result = duel.attack(
        MagicMock(), "bob", "#a", MagicMock(), MockConn(), "shoot"
    )
    assert "Stop shooting the dead" in result
    assert ("#a", "bob") not in duel.too_late_to_bang


def test_attack_shoot_from_grave_no_start_time():
    duel.too_late_to_bang[("#a", "bob")] = {
        "start_time": None,
        "result": "loose",
    }
    result = duel.attack(
        MagicMock(), "bob", "#a", MagicMock(), MockConn(), "shoot"
    )
    assert result == "bob You cannot shoot from the grave!"


def test_attack_too_late_recent():
    duel.too_late_to_bang[("#a", "bob")] = {
        "start_time": duel.time() - 0.1,
        "result": "loose",
    }
    result = duel.attack(
        MagicMock(), "bob", "#a", MagicMock(), MockConn(), "shoot"
    )
    assert "too late to shoot and died" in result


def test_attack_shoot_from_grave_late():
    duel.too_late_to_bang[("#a", "bob")] = {
        "start_time": duel.time() - 5,
        "result": "loose",
    }
    result = duel.attack(
        MagicMock(), "bob", "#a", MagicMock(), MockConn(), "shoot"
    )
    assert "You cannot shoot from the grave! You took" in result


def test_attack_no_game_running():
    conn = MockConn()
    result = duel.attack(
        MagicMock(), "bob", "#a", MagicMock(), conn, "shoot", "alice"
    )
    assert (
        result == "There is no duel right now. Use .startduel to start a game."
    )


def test_attack_self():
    conn = MockConn()
    enable_game(conn, "#a")
    result = duel.attack(
        MagicMock(), "bob", "#a", MagicMock(), conn, "shoot", "bob"
    )
    assert result == "http://www.suicide.org/"


def test_attack_no_opponent():
    conn = MockConn()
    enable_game(conn, "#a")
    result = duel.attack(MagicMock(), "bob", "#a", MagicMock(), conn, "shoot")
    assert "not in a duel with that person" in result


def test_attack_target_not_in_duel():
    conn = MockConn()
    enable_game(conn, "#a")
    result = duel.attack(
        MagicMock(), "bob", "#a", MagicMock(), conn, "shoot", "alice"
    )
    assert "not in a duel with that person" in result


def test_attack_not_started_yet():
    conn = MockConn()
    duel.duel_opponents["bob"] = "alice"
    duel.duel_opponents["alice"] = "bob"
    duel.duel_games[duel.duel_tuple("bob", "alice")] = {
        "chan": "#a",
        "nicks": ["bob", "alice"],
        "open": False,
        "start_time": None,
        "canceled": False,
    }
    enable_game(conn, "#a")
    result = duel.attack(
        MagicMock(), "bob", "#a", MagicMock(), conn, "shoot", "alice"
    )
    assert "not in a duel with that person" in result


def test_attack_shot_early(mock_db):
    duel.table.create(mock_db.engine, checkfirst=True)
    conn = MockConn()
    make_ready_duel(
        conn, "#a", "bob", "alice", open_=False, start_time=duel.time()
    )
    event = MagicMock()
    db = mock_db.session()
    with patch("plugins.duel.random.choice", return_value="You lost!"):
        result = duel.attack(event, "bob", "#a", db, conn, "shoot", "alice")
    assert result is None
    event.message.assert_called_once()
    text = event.message.call_args.args[0]
    assert "You shot too early!" in text
    assert "You lost!" in text
    assert "alice wins this duel and killed 1 here in #a" in text
    assert mock_db.get_data(duel.table) == [(conn.name, "alice", 1, "#a")]


def test_attack_success(mock_db):
    duel.table.create(mock_db.engine, checkfirst=True)
    conn = MockConn()
    make_ready_duel(
        conn, "#chan", "bob", "alice", open_=True, start_time=duel.time() - 3
    )
    event = MagicMock()
    db = mock_db.session()
    result = duel.attack(event, "bob", "#chan", db, conn, "shoot", "alice")
    assert result is None
    event.message.assert_called_once()
    text = event.message.call_args.args[0]
    assert "bob shot first" in text
    assert "killed alice" in text
    assert mock_db.get_data(duel.table) == [(conn.name, "bob", 1, "#chan")]
    assert duel.duel_tuple("bob", "alice") not in duel.duel_games
    assert duel.too_late_to_bang[("#chan", "bob")]["result"] == "win"
    assert duel.too_late_to_bang[("#chan", "alice")]["result"] == "loose"


def test_attack_update_score_error_reraised(mock_db):
    duel.table.create(mock_db.engine, checkfirst=True)
    conn = MockConn()
    make_ready_duel(
        conn, "#chan", "bob", "alice", open_=True, start_time=duel.time() - 3
    )
    event = MagicMock()
    db = mock_db.session()
    with patch(
        "plugins.duel.update_score", side_effect=RuntimeError("db down")
    ):
        with pytest.raises(RuntimeError):
            duel.attack(event, "bob", "#chan", db, conn, "shoot", "alice")
    status = duel.get_state_table(conn.name, "#chan")
    assert status.duel_status == 1
    event.reply.assert_called_once_with("An unknown error has occurred.")


def test_attack_invalid_type():
    conn = MockConn()
    with pytest.raises(NotImplementedError):
        duel.attack(
            MagicMock(), "bob", "#a", MagicMock(), conn, "befriend", "alice"
        )


def test_bang_wrong_prefix():
    conn = MockConn()
    conn.config["command_prefix"] = "!"
    pattern = re.compile(r"^\s*(.+)bang\s+(\S+)\s*$", re.I)
    match = pattern.search(".bang alice")
    with patch("plugins.duel.attack") as attack_mock:
        result = duel.bang(match, "bob", "#a", MagicMock(), conn, MagicMock())
    assert result is None
    attack_mock.assert_not_called()


def test_bang_delegates_to_attack():
    conn = MockConn()
    pattern = re.compile(r"^\s*(.+)bang\s+(\S+)\s*$", re.I)
    match = pattern.search(".bang alice")
    db = MagicMock()
    event = MagicMock()
    with patch("plugins.duel.attack", return_value="sentinel") as attack_mock:
        result = duel.bang(match, "bob", "#a", db, conn, event)
    assert result == "sentinel"
    attack_mock.assert_called_once_with(
        event, "bob", "#a", db, conn, "shoot", "alice"
    )
