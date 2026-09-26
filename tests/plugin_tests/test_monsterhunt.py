from unittest.mock import MagicMock, patch

import pytest

from plugins import monsterhunt
from tests.util.mock_conn import MockConn


@pytest.fixture(autouse=True)
def reset_module_state():
    monsterhunt.game_status.clear()
    monsterhunt.opt_out.clear()
    monsterhunt.scripters.clear()
    yield
    monsterhunt.game_status.clear()
    monsterhunt.opt_out.clear()
    monsterhunt.scripters.clear()


class TestLoadOptout:
    def test_load_optout(self, mock_db):
        monsterhunt.optout.create(mock_db.engine)
        mock_db.add_row(monsterhunt.optout, network="net", chan="#chan")
        monsterhunt.load_optout(mock_db.session())
        assert monsterhunt.opt_out == ["#chan"]


class TestIncrementMsgCounter:
    def test_opted_out_channel_ignored(self):
        monsterhunt.opt_out.append("#chan")
        conn = MockConn(name="net")
        event = MagicMock()
        event.chan = "#chan"
        event.host = "host"
        monsterhunt.incrementMsgCounter(event, conn)
        state = monsterhunt._get_state("net", "#chan")
        assert state.messages == 0

    def test_increments_while_hunt_active(self):
        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 0
        event = MagicMock()
        event.chan = "#chan"
        event.host = "host!user@example.com"
        monsterhunt.incrementMsgCounter(event, conn)
        monsterhunt.incrementMsgCounter(event, conn)
        assert state.messages == 2
        assert state.masks == ["host!user@example.com"]

    def test_ignored_when_hunt_inactive(self):
        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        event = MagicMock()
        event.chan = "#chan"
        event.host = "host"
        monsterhunt.incrementMsgCounter(event, conn)
        assert state.messages == 0
        assert state.masks == []


class TestStartHunt:
    def test_opted_out(self):
        monsterhunt.opt_out.append("#chan")
        conn = MockConn(name="net")
        message = MagicMock()
        result = monsterhunt.start_hunt("#chan", message, conn)
        assert result is None
        message.assert_not_called()

    def test_non_channel(self):
        conn = MockConn(name="net")
        message = MagicMock()
        result = monsterhunt.start_hunt("nick", message, conn)
        assert result == "No hunting by yourself, that isn't safe."

    def test_already_running(self):
        conn = MockConn(name="net")
        monsterhunt._get_state("net", "#chan").game_on = 1
        message = MagicMock()
        result = monsterhunt.start_hunt("#chan", message, conn)
        assert result == "There is already a hunt running in #chan."

    def test_success(self):
        conn = MockConn(name="net")
        message = MagicMock()
        result = monsterhunt.start_hunt("#chan", message, conn)
        assert result is None
        state = monsterhunt._get_state("net", "#chan")
        assert state.game_on == 1
        assert state.duck_status == 0
        message.assert_called_once_with(
            "Monsters have been spotted chasing people. Save the person "
            "and kill the monster with .bang, or try and befriend the "
            "monster and let it kill the person using .befriend. For "
            "more information on this creepy event see "
            "https://redd.it/56sqlx",
            "#chan",
        )


class TestSetDucktime:
    def test_resets_state_within_bounds(self, freeze_time):
        import time as time_module

        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.duck_status = 1
        state.messages = 5
        state.masks = ["a", "b"]
        now = int(time_module.time())
        monsterhunt.set_ducktime("#chan", conn)
        assert state.duck_status == 0
        assert state.messages == 0
        assert state.masks == []
        assert now + 480 <= state.next_duck_time <= now + 3600


class TestStopHunt:
    def test_opted_out(self):
        monsterhunt.opt_out.append("#chan")
        conn = MockConn(name="net")
        assert monsterhunt.stop_hunt("#chan", conn) is None

    def test_running(self):
        conn = MockConn(name="net")
        monsterhunt._get_state("net", "#chan").game_on = 1
        result = monsterhunt.stop_hunt("#chan", conn)
        assert result == "The hunt has been stopped."
        assert monsterhunt._get_state("net", "#chan").game_on == 0

    def test_not_running(self):
        conn = MockConn(name="net")
        result = monsterhunt.stop_hunt("#chan", conn)
        assert result == "There is no monster hunt running in #chan."


class TestNoDuckKick:
    def test_opted_out(self):
        monsterhunt.opt_out.append("#chan")
        conn = MockConn(name="net")
        notice = MagicMock()
        assert monsterhunt.no_duck_kick("enable", "#chan", conn, notice) is (
            None
        )
        notice.assert_not_called()

    def test_enable(self):
        conn = MockConn(name="net")
        notice = MagicMock()
        result = monsterhunt.no_duck_kick("enable", "#chan", conn, notice)
        assert result == (
            "users will now be kicked for shooting or befriending "
            "non-existent monsters. The bot needs to have appropriate "
            "flags to be able to kick users for this to work."
        )
        assert monsterhunt._get_state("net", "#chan").no_duck_kick == 1

    def test_disable(self):
        conn = MockConn(name="net")
        notice = MagicMock()
        result = monsterhunt.no_duck_kick("disable", "#chan", conn, notice)
        assert result == (
            "kicking for non-existent monsters has been disabled."
        )
        assert monsterhunt._get_state("net", "#chan").no_duck_kick == 0

    def test_unrecognized_shows_help(self):
        conn = MockConn(name="net")
        notice = MagicMock()
        result = monsterhunt.no_duck_kick("bogus", "#chan", conn, notice)
        assert result is None
        notice.assert_called_once_with(monsterhunt.no_duck_kick.__doc__)


class TestGenerateDuck:
    def test_generates_pieces_with_zero_width_space(self):
        dtail, dbody, dnoise = monsterhunt.generate_duck()
        assert "\u200b" in dtail
        assert "\u200b" in dbody
        assert "\u200b" in dnoise


class TestDeployDuck:
    def test_skips_unconnected_network(self):
        monsterhunt._get_state("net", "#chan")
        bot = MagicMock()
        bot.connections = {}
        monsterhunt.deploy_duck(bot)

    def test_skips_not_ready(self):
        conn = MockConn(name="net")
        conn.ready = False
        conn.message = MagicMock()
        monsterhunt._get_state("net", "#chan")
        bot = MagicMock()
        bot.connections = {"net": conn}
        monsterhunt.deploy_duck(bot)
        conn.message.assert_not_called()

    def test_skips_when_conditions_not_met(self):
        conn = MockConn(name="net")
        conn.message = MagicMock()
        monsterhunt._get_state("net", "#chan")
        bot = MagicMock()
        bot.connections = {"net": conn}
        monsterhunt.deploy_duck(bot)
        conn.message.assert_not_called()

    def test_deploys_when_conditions_met(self, freeze_time):
        conn = MockConn(name="net")
        conn.message = MagicMock()
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 0
        state.next_duck_time = 0
        state.messages = monsterhunt.MSG_DELAY
        state.masks = ["a", "b", "c"]
        bot = MagicMock()
        bot.connections = {"net": conn}

        monsterhunt.deploy_duck(bot)

        assert state.duck_status == 1
        conn.message.assert_called_once()
        args = conn.message.call_args.args
        assert args[0] == "#chan"
        assert isinstance(args[1], str) and args[1]


class TestHitOrMiss:
    def test_instant_shot_is_suspicious(self):
        assert monsterhunt.hit_or_miss(10.0, 10.5) == 0.05

    def test_normal_window(self):
        result = monsterhunt.hit_or_miss(10.0, 15.0)
        assert 0.60 <= result <= 0.75

    def test_slow_shot_is_certain(self):
        assert monsterhunt.hit_or_miss(10.0, 20.0) == 1


class TestDbHelpers:
    def test_dbadd_entry(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("Nick", "#Chan", db, conn, 2, 1)
        assert mock_db.get_data(monsterhunt.table) == [
            ("net", "nick", 2, 1, "#chan")
        ]

    def test_dbupdate_shoot_only(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("nick", "#chan", db, conn, 1, 5)
        monsterhunt.dbupdate("nick", "#chan", db, conn, 9, 0)
        assert mock_db.get_data(monsterhunt.table) == [
            ("net", "nick", 9, 5, "#chan")
        ]

    def test_dbupdate_friend_only(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("nick", "#chan", db, conn, 5, 1)
        monsterhunt.dbupdate("nick", "#chan", db, conn, 0, 9)
        assert mock_db.get_data(monsterhunt.table) == [
            ("net", "nick", 5, 9, "#chan")
        ]

    def test_dbupdate_both(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("nick", "#chan", db, conn, 1, 1)
        monsterhunt.dbupdate("nick", "#chan", db, conn, 7, 8)
        assert mock_db.get_data(monsterhunt.table) == [
            ("net", "nick", 7, 8, "#chan")
        ]


class TestBang:
    def test_opted_out(self, mock_db):
        monsterhunt.opt_out.append("#chan")
        conn = MockConn(name="net")
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        result = monsterhunt.bang("nick", "#chan", message, db, conn, notice)
        assert result is None
        message.assert_not_called()

    def test_no_hunt_running(self, mock_db):
        conn = MockConn(name="net")
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        result = monsterhunt.bang("nick", "#chan", message, db, conn, notice)
        assert result == (
            "There is no activehunt right now. Use .starthunt to start a game."
        )

    def test_no_monster_kicks_when_enabled(self, mock_db):
        conn = MockConn(name="net")
        conn.send = MagicMock()
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 0
        state.no_duck_kick = 1
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        result = monsterhunt.bang("nick", "#chan", message, db, conn, notice)
        assert result is None
        conn.send.assert_called_once_with(
            "KICK #chan nick There is no monster! What are you shooting at?"
        )

    def test_no_monster_message_when_kick_disabled(self, mock_db):
        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 0
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        result = monsterhunt.bang("nick", "#chan", message, db, conn, notice)
        assert result == "There is no monster. What are you shooting at?"

    def test_scripter_cooldown(self, mock_db, freeze_time):
        import time as time_module

        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time() - 100
        monsterhunt.scripters["nick"] = time_module.time() + 500
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        result = monsterhunt.bang("nick", "#chan", message, db, conn, notice)
        assert result is None
        notice.assert_called_once()
        assert "cool down period" in notice.call_args.args[0]

    def test_miss(self, mock_db, freeze_time):
        import time as time_module

        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time() - 3600
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        with patch.object(monsterhunt, "hit_or_miss", return_value=0.5):
            with patch.object(monsterhunt.random, "random", return_value=0.9):
                result = monsterhunt.bang(
                    "nick", "#chan", message, db, conn, notice
                )
        assert result.endswith("You can try again in 7 seconds.")
        assert monsterhunt.scripters["nick"] > time_module.time()

    def test_success_first_kill(self, mock_db, freeze_time):
        import time as time_module

        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time() - 3600
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        with patch.object(monsterhunt, "hit_or_miss", return_value=1):
            result = monsterhunt.bang(
                "nick", "#chan", message, db, conn, notice
            )
        assert result is None
        message.assert_called_once_with(
            "nick you shot a monster and saved a human in 3600.000 "
            "seconds! You have killed 1 monster in #chan."
        )
        assert mock_db.get_data(monsterhunt.table) == [
            ("net", "nick", 1, 0, "#chan")
        ]
        assert state.duck_status == 0

    def test_success_increments_existing_score(self, mock_db, freeze_time):
        import time as time_module

        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("nick", "#chan", db, conn, 4, 0)
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time() - 3600
        message = MagicMock()
        notice = MagicMock()
        with patch.object(monsterhunt, "hit_or_miss", return_value=1):
            monsterhunt.bang("nick", "#chan", message, db, conn, notice)
        message.assert_called_once_with(
            "nick you shot a monster and saved a human in 3600.000 "
            "seconds! You have killed 5 monsters in #chan."
        )

    def test_fast_shot_flagged_as_suspicious_and_fails(
        self, mock_db, freeze_time
    ):
        import time as time_module

        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time()
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        with patch.object(monsterhunt.random, "random", return_value=0.5):
            result = monsterhunt.bang(
                "nick", "#chan", message, db, conn, notice
            )
        assert "mighty fast" in result
        assert monsterhunt.scripters["nick"] == pytest.approx(
            time_module.time() + 7200
        )

    def test_fast_shot_flagged_as_suspicious_but_succeeds(
        self, mock_db, freeze_time
    ):
        import time as time_module

        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time()
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        with patch.object(monsterhunt.random, "random", return_value=0.01):
            result = monsterhunt.bang(
                "nick", "#chan", message, db, conn, notice
            )
        assert result is None
        assert "mighty fast" in message.mock_calls[0].args[0]
        assert mock_db.get_data(monsterhunt.table) == [
            ("net", "nick", 1, 0, "#chan")
        ]


class TestBefriend:
    def test_opted_out(self, mock_db):
        monsterhunt.opt_out.append("#chan")
        conn = MockConn(name="net")
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        result = monsterhunt.befriend(
            "nick", "#chan", message, db, conn, notice
        )
        assert result is None

    def test_no_hunt_running(self, mock_db):
        conn = MockConn(name="net")
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        result = monsterhunt.befriend(
            "nick", "#chan", message, db, conn, notice
        )
        assert (
            result
            == "There is no hunt right now. Use .starthunt to start a game."
        )

    def test_no_monster_kicks_when_enabled(self, mock_db):
        conn = MockConn(name="net")
        conn.send = MagicMock()
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 0
        state.no_duck_kick = 1
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        result = monsterhunt.befriend(
            "nick", "#chan", message, db, conn, notice
        )
        assert result is None
        conn.send.assert_called_once_with(
            "KICK #chan nick You tried befriending a non-existent "
            "monster, that's fucking creepy."
        )

    def test_no_monster_message_when_kick_disabled(self, mock_db):
        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 0
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        result = monsterhunt.befriend(
            "nick", "#chan", message, db, conn, notice
        )
        assert result == (
            "You tried befriending a non-existent monster, that's "
            "fucking creepy."
        )

    def test_scripter_cooldown(self, mock_db, freeze_time):
        import time as time_module

        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time() - 100
        monsterhunt.scripters["nick"] = time_module.time() + 500
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        result = monsterhunt.befriend(
            "nick", "#chan", message, db, conn, notice
        )
        assert result is None
        notice.assert_called_once()
        assert "cool down period" in notice.call_args.args[0]

    def test_miss(self, mock_db, freeze_time):
        import time as time_module

        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time() - 3600
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        with patch.object(monsterhunt, "hit_or_miss", return_value=0.5):
            with patch.object(monsterhunt.random, "random", return_value=0.9):
                result = monsterhunt.befriend(
                    "nick", "#chan", message, db, conn, notice
                )
        assert result.endswith("You can try again in 7 seconds.")

    def test_fast_befriend_flagged_as_suspicious_but_succeeds(
        self, mock_db, freeze_time
    ):
        import time as time_module

        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time()
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        with patch.object(monsterhunt.random, "random", return_value=0.01):
            result = monsterhunt.befriend(
                "nick", "#chan", message, db, conn, notice
            )
        assert result is None
        assert "mighty fast" in message.mock_calls[0].args[0]

    def test_fast_befriend_flagged_as_suspicious_and_fails(
        self, mock_db, freeze_time
    ):
        import time as time_module

        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time()
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        with patch.object(monsterhunt.random, "random", return_value=0.5):
            result = monsterhunt.befriend(
                "nick", "#chan", message, db, conn, notice
            )
        assert "mighty fast" in result
        message.assert_not_called()

    def test_success_increments_existing_score(self, mock_db, freeze_time):
        import time as time_module

        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("nick", "#chan", db, conn, 0, 4)
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time() - 3600
        message = MagicMock()
        notice = MagicMock()
        with patch.object(monsterhunt, "hit_or_miss", return_value=1):
            monsterhunt.befriend("nick", "#chan", message, db, conn, notice)
        message.assert_called_once_with(
            "nick you befriended a monster in 3600.000 seconds! You "
            "have made friends with 5 monsters in #chan."
        )

    def test_success(self, mock_db, freeze_time):
        import time as time_module

        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        state = monsterhunt._get_state("net", "#chan")
        state.game_on = 1
        state.duck_status = 1
        state.duck_time = time_module.time() - 3600
        db = mock_db.session()
        message = MagicMock()
        notice = MagicMock()
        with patch.object(monsterhunt, "hit_or_miss", return_value=1):
            result = monsterhunt.befriend(
                "nick", "#chan", message, db, conn, notice
            )
        assert result is None
        message.assert_called_once_with(
            "nick you befriended a monster in 3600.000 seconds! You "
            "have made friends with 1 monster in #chan."
        )
        assert mock_db.get_data(monsterhunt.table) == [
            ("net", "nick", 0, 1, "#chan")
        ]


class TestSmartTruncate:
    def test_short_content_unchanged(self):
        assert monsterhunt.smart_truncate("short") == "short"

    def test_long_content_truncated_at_separator(self):
        content = ("word • " * 100).strip()
        result = monsterhunt.smart_truncate(content)
        expected = content[:320].rsplit(" • ", 1)[0] + "..."
        assert result == expected


class TestFriendsKillers:
    def _seed(self, mock_db, conn):
        monsterhunt.table.create(mock_db.engine)
        db = mock_db.session()
        monsterhunt.dbadd_entry("alice", "#chan", db, conn, 5, 4)
        monsterhunt.dbadd_entry("bob", "#chan", db, conn, 1, 7)
        monsterhunt.dbadd_entry("carol", "#other", db, conn, 9, 2)
        return db

    def test_friends_opted_out(self, mock_db):
        monsterhunt.opt_out.append("#chan")
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.table.create(mock_db.engine)
        assert monsterhunt.friends("", "#chan", conn, db) is None

    def test_killers_opted_out(self, mock_db):
        monsterhunt.opt_out.append("#chan")
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.table.create(mock_db.engine)
        assert monsterhunt.killers("", "#chan", conn, db) is None

    def test_friends_empty(self, mock_db, patch_paste):
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.table.create(mock_db.engine)
        result = monsterhunt.friends("", "#chan", conn, db)
        assert result == "it appears no one has friended any monsters yet."
        patch_paste.assert_not_called()

    def test_friends_empty_global(self, mock_db, patch_paste):
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.table.create(mock_db.engine)
        result = monsterhunt.friends("global", "#chan", conn, db)
        assert result == "it appears no one has friended any monsters yet."
        patch_paste.assert_not_called()

    def test_friends_average_skips_zero_scores(self, mock_db, patch_paste):
        conn = MockConn(name="net")
        db = self._seed(mock_db, conn)
        monsterhunt.dbadd_entry("dave", "#chan", db, conn, 0, 0)
        patch_paste.return_value = "http://paste.example/z"
        result = monsterhunt.friends("average", "#chan", conn, db)
        assert result == (
            "Monster friend scores across the network: "
            "\x02b\u200bob\x02: 7 • \x02a\u200blice\x02: 4 • "
            "\x02c\u200barol\x02: 2 http://paste.example/z"
        )
        assert "dave" not in result

    def test_friends_channel(self, mock_db, patch_paste):
        conn = MockConn(name="net")
        db = self._seed(mock_db, conn)
        patch_paste.return_value = "http://paste.example/x"
        result = monsterhunt.friends("", "#chan", conn, db)
        assert result == (
            "Monster friend scores in #chan: "
            "\x02b\u200bob\x02: 7 • \x02a\u200blice\x02: 4 "
            "http://paste.example/x"
        )

    def test_friends_channel_skips_zero_scores(self, mock_db, patch_paste):
        conn = MockConn(name="net")
        db = self._seed(mock_db, conn)
        monsterhunt.dbadd_entry("dave", "#chan", db, conn, 0, 0)
        patch_paste.return_value = "http://paste.example/v"
        result = monsterhunt.friends("", "#chan", conn, db)
        assert result == (
            "Monster friend scores in #chan: "
            "\x02b\u200bob\x02: 7 • \x02a\u200blice\x02: 4 "
            "http://paste.example/v"
        )

    def test_friends_global(self, mock_db, patch_paste):
        conn = MockConn(name="net")
        db = self._seed(mock_db, conn)
        patch_paste.return_value = "http://paste.example/x"
        result = monsterhunt.friends("global", "#chan", conn, db)
        assert result == (
            "Monster friend scores across the network: "
            "\x02b\u200bob\x02: 7 • \x02a\u200blice\x02: 4 • "
            "\x02c\u200barol\x02: 2 http://paste.example/x"
        )

    def test_killers_empty(self, mock_db, patch_paste):
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.table.create(mock_db.engine)
        result = monsterhunt.killers("", "#chan", conn, db)
        assert result == "it appears no on has killed any monsters yet."

    def test_killers_empty_global(self, mock_db, patch_paste):
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.table.create(mock_db.engine)
        result = monsterhunt.killers("global", "#chan", conn, db)
        assert result == "it appears no one has killed any monsters yet."

    def test_killers_average(self, mock_db, patch_paste):
        conn = MockConn(name="net")
        db = self._seed(mock_db, conn)
        monsterhunt.dbadd_entry("dave", "#chan", db, conn, 0, 0)
        patch_paste.return_value = "http://paste.example/y"
        result = monsterhunt.killers("average", "#chan", conn, db)
        assert result == (
            "Monster killer scores across the network: "
            "\x02c\u200barol\x02: 9 • \x02a\u200blice\x02: 5 • "
            "\x02b\u200bob\x02: 1 http://paste.example/y"
        )
        assert "dave" not in result

    def test_killers_channel_skips_zero_scores(self, mock_db, patch_paste):
        conn = MockConn(name="net")
        db = self._seed(mock_db, conn)
        monsterhunt.dbadd_entry("dave", "#chan", db, conn, 0, 0)
        patch_paste.return_value = "http://paste.example/w"
        result = monsterhunt.killers("", "#chan", conn, db)
        assert result == (
            "Monster killer scores in #chan: "
            "\x02a\u200blice\x02: 5 • \x02b\u200bob\x02: 1 "
            "http://paste.example/w"
        )


class TestDuckforgive:
    def test_not_in_cooldown(self):
        result = monsterhunt.duckforgive("nick")
        assert result == (
            "I couldn't find anyone banned from the hunt by that nick"
        )

    def test_removes_active_cooldown(self, freeze_time):
        import time as time_module

        monsterhunt.scripters["nick"] = time_module.time() + 1000
        result = monsterhunt.duckforgive("nick")
        assert result == (
            "nick has been removed from the mandatory cooldown period."
        )
        assert monsterhunt.scripters["nick"] == 0


class TestHuntOptOut:
    def test_status_enabled(self, mock_db):
        conn = MockConn(name="net")
        result = monsterhunt.hunt_opt_out("", "#chan", mock_db.session(), conn)
        assert result == (
            "Monster hunt is enabled in #chan. To disable it run "
            ".hunt_opt_out add #channel"
        )

    def test_status_disabled(self, mock_db):
        monsterhunt.opt_out.append("#chan")
        conn = MockConn(name="net")
        result = monsterhunt.hunt_opt_out("", "#chan", mock_db.session(), conn)
        assert result == (
            "Monster hunt is disabled in #chan. To re-enable it run "
            ".hunt_opt_out remove #channel"
        )

    def test_list(self, mock_db):
        monsterhunt.opt_out.extend(["#a", "#b"])
        conn = MockConn(name="net")
        result = monsterhunt.hunt_opt_out(
            "list", "#chan", mock_db.session(), conn
        )
        assert result == "#a, #b"

    def test_missing_args(self, mock_db):
        conn = MockConn(name="net")
        result = monsterhunt.hunt_opt_out(
            "add", "#chan", mock_db.session(), conn
        )
        assert result == "please specify add or remove and a valid channel name"

    def test_invalid_channel(self, mock_db):
        conn = MockConn(name="net")
        result = monsterhunt.hunt_opt_out(
            "add notachannel", "#chan", mock_db.session(), conn
        )
        assert result == "Please specify a valid channel."

    def test_add(self, mock_db):
        monsterhunt.optout.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        result = monsterhunt.hunt_opt_out("add #foo", "#chan", db, conn)
        assert result == (
            "The monster hunt has been successfully disabled in #foo."
        )
        assert monsterhunt.opt_out == ["#foo"]
        assert mock_db.get_data(monsterhunt.optout) == [("net", "#foo")]

    def test_add_already_disabled(self, mock_db):
        monsterhunt.optout.create(mock_db.engine)
        monsterhunt.opt_out.append("#foo")
        conn = MockConn(name="net")
        db = mock_db.session()
        result = monsterhunt.hunt_opt_out("add #foo", "#chan", db, conn)
        assert result == "Monster hunt has already been disabled in #foo."

    def test_remove(self, mock_db):
        monsterhunt.optout.create(mock_db.engine)
        mock_db.add_row(monsterhunt.optout, network="net", chan="#foo")
        monsterhunt.opt_out.append("#foo")
        conn = MockConn(name="net")
        db = mock_db.session()
        result = monsterhunt.hunt_opt_out("remove #foo", "#chan", db, conn)
        assert result is None
        assert monsterhunt.opt_out == []
        assert mock_db.get_data(monsterhunt.optout) == []

    def test_remove_already_enabled(self, mock_db):
        monsterhunt.optout.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        result = monsterhunt.hunt_opt_out("remove #foo", "#chan", db, conn)
        assert result == "Monster hunt is already enabled in #foo."

    def test_unknown_command(self, mock_db):
        conn = MockConn(name="net")
        db = mock_db.session()
        result = monsterhunt.hunt_opt_out("frobnicate #foo", "#chan", db, conn)
        assert result is None


class TestDuckMerge:
    def test_same_nick(self, mock_db):
        conn = MockConn(name="net")
        message = MagicMock()
        result = monsterhunt.duck_merge(
            "alice alice", conn, mock_db.session(), message
        )
        assert result == "please specify two different nicks."

    def test_no_data(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        message = MagicMock()
        result = monsterhunt.duck_merge(
            "alice bob", conn, mock_db.session(), message
        )
        assert result == "There are no monster scores to migrate from alice"
        message.assert_not_called()

    def test_merges_into_new_nick_without_existing_score(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("alice", "#chan", db, conn, 5, 3)
        message = MagicMock()
        result = monsterhunt.duck_merge("alice bob", conn, db, message)
        assert result is None
        message.assert_called_once_with(
            "Migrated 5 monster kills and 3 monster friends from alice to bob"
        )
        assert mock_db.get_data(monsterhunt.table) == [
            ("net", "bob", 5, 3, "#chan")
        ]

    def test_merges_summing_with_existing_score(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("alice", "#chan", db, conn, 5, 3)
        monsterhunt.dbadd_entry("bob", "#chan", db, conn, 1, 1)
        message = MagicMock()
        result = monsterhunt.duck_merge("alice bob", conn, db, message)
        assert result is None
        message.assert_called_once_with(
            "Migrated 5 monster kills and 3 monster friends from alice to bob"
        )
        assert mock_db.get_data(monsterhunt.table) == [
            ("net", "bob", 6, 4, "#chan")
        ]

    def test_merges_new_channel_while_target_has_other_scores(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("alice", "#chan", db, conn, 5, 3)
        monsterhunt.dbadd_entry("bob", "#other", db, conn, 2, 1)
        message = MagicMock()
        result = monsterhunt.duck_merge("alice bob", conn, db, message)
        assert result is None
        message.assert_called_once_with(
            "Migrated 5 monster kills and 3 monster friends from alice to bob"
        )
        assert mock_db.get_data(monsterhunt.table) == [
            ("net", "bob", 2, 1, "#other"),
            ("net", "bob", 5, 3, "#chan"),
        ]


class TestDucksUser:
    def test_no_participation(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        message = MagicMock()
        result = monsterhunt.ducks_user("", "alice", "#chan", conn, db, message)
        assert result == (
            "It appears alice has not participated in the monster hunt."
        )

    def test_single_channel(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("alice", "#chan", db, conn, 4, 2)
        message = MagicMock()
        result = monsterhunt.ducks_user("", "alice", "#chan", conn, db, message)
        assert result is None
        message.assert_called_once_with(
            "alice has killed 4 and befriended 2 monsters in #chan."
        )

    def test_multi_channel(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("alice", "#chan", db, conn, 4, 2)
        monsterhunt.dbadd_entry("alice", "#other", db, conn, 6, 0)
        message = MagicMock()
        result = monsterhunt.ducks_user(
            "alice extra", "bob", "#chan", conn, db, message
        )
        assert result is None
        message.assert_called_once_with(
            "\x02alice's\x02 duck stats: \x024\x02 killed and \x022\x02 "
            "befriended in #chan. Across 2 channels: \x0210\x02 killed "
            "and \x022\x02 befriended. Averaging \x025\x02 kills and "
            "\x021\x02 friends per channel."
        )


class TestDuckStats:
    def test_no_activity(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        message = MagicMock()
        result = monsterhunt.duck_stats("#chan", conn, db, message)
        assert result == (
            "It looks like there has been no monster activity on this "
            "channel or network."
        )

    def test_with_activity(self, mock_db):
        monsterhunt.table.create(mock_db.engine)
        conn = MockConn(name="net")
        db = mock_db.session()
        monsterhunt.dbadd_entry("alice", "#chan", db, conn, 4, 2)
        monsterhunt.dbadd_entry("bob", "#other", db, conn, 9, 5)
        message = MagicMock()
        result = monsterhunt.duck_stats("#chan", conn, db, message)
        assert result is None
        message.assert_called_once_with(
            "\x02Monster Stats:\x02 4 killed and 2 befriended in "
            "\x02#chan\x02. Across 2 channels \x0213\x02 monsters have "
            "been killed and \x027\x02 befriended. \x02Top Channels:\x02 "
            "\x02#other\x02 with 9 kills and \x02#other\x02 with 5 "
            "friends"
        )
