import datetime
import re
from unittest.mock import MagicMock, call, patch

import pytest

from cloudbot.event import EventType
from cloudbot.util import web
from plugins import beans
from tests.util.mock_conn import MockConn

BASE_URL = "http://beapin.test"


def make_bot(
    base_url=BASE_URL, admin_api_key="testkey", bot_username="cloudbot"
):
    bot = MagicMock()
    bot.config = {
        "plugins": {
            "beapin": {
                "api_url": base_url,
                "admin_api_key": admin_api_key,
                "bot_username": bot_username,
            }
        }
    }
    return bot


def users_url():
    return BASE_URL + beans.BeapinAPI.ENDPOINT_ADMIN_USERS


def total_url():
    return BASE_URL + beans.BeapinAPI.ENDPOINT_TOTAL


def giftlinks_url():
    return BASE_URL + beans.BeapinAPI.ENDPOINT_GIFTLINKS


def harvests_url():
    return BASE_URL + beans.BeapinAPI.ENDPOINT_HARVESTS


@pytest.fixture(autouse=True)
def reset_slot_cache():
    beans.slot_cooldown_cache.clear()
    yield
    beans.slot_cooldown_cache.clear()


def test_strip_nick():
    assert beans.strip_nick("foo") == "foo"
    assert beans.strip_nick("network/foo") == "foo"
    assert beans.strip_nick("a/b/c") == "c"


class TestBeapinClient:
    def test_get_all_users(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "alice", "bean_amount": 10}],
        )
        assert client.get_all_users() == [
            {"username": "alice", "bean_amount": 10}
        ]
        assert mock_requests.calls[0].request.headers["Authorization"] == (
            "Bearer testkey"
        )

    def test_get_all_users_failure(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add("GET", users_url(), status=500)
        assert client.get_all_users() is None

    def test_get_all_users_connection_error(self, mock_requests):
        import requests

        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add(
            "GET", users_url(), body=requests.ConnectionError("boom")
        )
        assert client.get_all_users() is None

    def test_get_user_balance_found(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "Alice", "bean_amount": 42}],
        )
        assert client.get_user_balance("network/alice") == 42

    def test_get_user_balance_not_found(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add(
            "GET", users_url(), json=[{"username": "bob", "bean_amount": 1}]
        )
        assert client.get_user_balance("alice") == 0

    def test_get_user_balance_no_users(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add("GET", users_url(), json=[])
        assert client.get_user_balance("alice") is None

    def test_get_bot_balance(self, mock_requests):
        bot = make_bot(bot_username="cloudbot")
        client = beans.BeapinClient(bot)
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 500}],
        )
        assert client.get_bot_balance() == 500

    def test_get_total_beans(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add("GET", total_url(), json={"total_beans": 999})
        assert client.get_total_beans() == 999

    def test_get_total_beans_failure(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add("GET", total_url(), status=503)
        assert client.get_total_beans() is None

    def test_create_gift_link_defaults(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add("POST", giftlinks_url(), json={"code": "abc123"})
        result = client.create_gift_link(50)
        assert result == {"code": "abc123"}
        payload = mock_requests.calls[0].request.body
        assert b'"amount": 50' in payload
        assert b'"expires_in": "7d"' in payload
        assert b"message" not in payload

    def test_create_gift_link_with_message_and_expiry(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add("POST", giftlinks_url(), json={"code": "xyz"})
        result = client.create_gift_link(10, "hello", "1d")
        assert result == {"code": "xyz"}
        payload = mock_requests.calls[0].request.body
        assert b'"message": "hello"' in payload
        assert b'"expires_in": "1d"' in payload

    def test_create_gift_link_empty_expiry_omitted(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add("POST", giftlinks_url(), json={"code": "n"})
        client.create_gift_link(10, expires_in="")
        payload = mock_requests.calls[0].request.body
        assert b"expires_in" not in payload

    def test_create_harvest(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add("POST", harvests_url(), json={"id": 7})
        assert client.create_harvest("title", "desc", 100) == 7

    def test_create_harvest_failure(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add("POST", harvests_url(), status=500)
        assert client.create_harvest("title", "desc", 100) is None

    def test_assign_harvest(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add(
            "POST", harvests_url() + "/7/assign", json={"ok": True}
        )
        result = client.assign_harvest(7, "net/alice")
        assert result == {"ok": True}
        payload = mock_requests.calls[0].request.body
        assert b'"username": "alice"' in payload

    def test_complete_harvest(self, mock_requests):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        mock_requests.add(
            "POST", harvests_url() + "/7/complete", json={"ok": True}
        )
        assert client.complete_harvest(7) == {"ok": True}

    def test_build_urls(self):
        bot = make_bot()
        client = beans.BeapinClient(bot)
        assert client.build_gift_url("code1") == BASE_URL + "/gift/code1"
        assert client.build_harvest_url(3) == BASE_URL + "/#harvest/3"
        assert (
            client.build_transfer_url("net/alice", "net/bob", 5)
            == BASE_URL + "/transfer/alice/bob/5"
        )


class TestLegacyWrappers:
    def test_get_beapin_config(self):
        bot = make_bot()
        assert beans.get_beapin_config(bot)["api_url"] == BASE_URL

    def test_get_api_headers(self):
        bot = make_bot(admin_api_key="secret")
        headers = beans.get_api_headers(bot)
        assert headers["Authorization"] == "Bearer secret"

    def test_create_transfer_url(self):
        bot = make_bot()
        url = beans.create_transfer_url(bot, "alice", "bob", 3)
        assert url == BASE_URL + "/transfer/alice/bob/3"

    def test_get_wallet_balance(self, mock_requests):
        bot = make_bot()
        mock_requests.add(
            "GET", users_url(), json=[{"username": "alice", "bean_amount": 3}]
        )
        assert beans.get_wallet_balance(bot, "alice") == 3

    def test_get_total_beans_wrapper(self, mock_requests):
        bot = make_bot()
        mock_requests.add("GET", total_url(), json={"total_beans": 5})
        assert beans.get_total_beans(bot) == 5

    def test_get_all_users_wrapper(self, mock_requests):
        bot = make_bot()
        mock_requests.add("GET", users_url(), json=[])
        assert beans.get_all_users(bot) == []

    def test_create_harvest_wrapper(self, mock_requests):
        bot = make_bot()
        mock_requests.add("POST", harvests_url(), json={"id": 1})
        assert beans.create_harvest(bot, "t", "d", 1) == 1

    def test_assign_harvest_to_user_wrapper(self, mock_requests):
        bot = make_bot()
        mock_requests.add(
            "POST", harvests_url() + "/1/assign", json={"ok": True}
        )
        assert beans.assign_harvest_to_user(bot, 1, "alice") == {"ok": True}

    def test_complete_harvest_wrapper(self, mock_requests):
        bot = make_bot()
        mock_requests.add(
            "POST", harvests_url() + "/1/complete", json={"ok": True}
        )
        assert beans.complete_harvest(bot, 1) == {"ok": True}

    def test_get_bot_wallet_balance_wrapper(self, mock_requests):
        bot = make_bot(bot_username="cloudbot")
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 8}],
        )
        assert beans.get_bot_wallet_balance(bot) == 8

    def test_create_gift_link_wrapper(self, mock_requests):
        bot = make_bot()
        mock_requests.add("POST", giftlinks_url(), json={"code": "z"})
        assert beans.create_gift_link(bot, 1) == {"code": "z"}


class TestSendPrizeGiftLink:
    def test_insufficient_bot_balance(self, mock_requests):
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 5}],
        )
        message_func = MagicMock()
        success, error, chan_msg = beans.send_prize_gift_link(
            bot, "alice", 10, "prize", message_func
        )
        assert success is False
        assert "doesn't have enough beans" in error
        assert chan_msg is None
        message_func.assert_not_called()

    def test_gift_link_creation_failure(self, mock_requests):
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 500}],
        )
        mock_requests.add("POST", giftlinks_url(), status=500)
        message_func = MagicMock()
        success, error, chan_msg = beans.send_prize_gift_link(
            bot, "alice", 10, "prize", message_func
        )
        assert success is False
        assert "Failed to create prize gift link" in error
        message_func.assert_not_called()

    def test_success(self, mock_requests):
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 500}],
        )
        mock_requests.add("POST", giftlinks_url(), json={"code": "gg1"})
        message_func = MagicMock()
        success, error, chan_msg = beans.send_prize_gift_link(
            bot, "alice", 10, "prize", message_func
        )
        assert success is True
        assert error is None
        assert chan_msg == "Check your DM to claim 🫘 10 beans!"
        assert message_func.mock_calls == [
            call("🎉 Congratulations! You won 🫘 10 beans!", "alice"),
            call("Claim your prize here: " + BASE_URL + "/gift/gg1", "alice"),
        ]


class TestLocalDb:
    def test_get_beans_default(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        assert beans.get_beans("Alice", db) == 0

    def test_set_and_get_beans(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        beans.set_beans("Alice", 10, db)
        assert beans.get_beans("alice", db) == 10
        beans.set_beans("ALICE", 25, db)
        assert beans.get_beans("alice", db) == 25
        assert mock_db.get_data(beans.beans_table) == [("alice", 25)]

    def test_transfer_beans_success(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        beans.set_beans("alice", 10, db)
        beans.set_beans("bob", 5, db)
        assert beans.transfer_beans("Alice", "Bob", 4, db) is True
        assert beans.get_beans("alice", db) == 6
        assert beans.get_beans("bob", db) == 9

    def test_transfer_beans_insufficient(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        beans.set_beans("alice", 1, db)
        assert beans.transfer_beans("alice", "bob", 4, db) is False
        assert beans.get_beans("alice", db) == 1
        assert beans.get_beans("bob", db) == 0

    def test_add_beans(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        beans.add_beans("alice", 3, db)
        beans.add_beans("alice", 4, db)
        assert beans.get_beans("alice", db) == 7

    def test_get_total_beans_db_empty(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        assert beans.get_total_beans_db(db) == 0

    def test_get_total_beans_db(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        beans.set_beans("alice", 3, db)
        beans.set_beans("bob", 4, db)
        assert beans.get_total_beans_db(db) == 7


class TestBeansCmd:
    def test_no_text_uses_nick(self, mock_requests):
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "alice", "bean_amount": 12}],
        )
        result = beans.beans_cmd("", "alice", bot)
        assert result == "🌟 alice has 🫘 12 beans! 🌟"

    def test_with_text_strips_network(self, mock_requests):
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "bob", "bean_amount": 1000}],
        )
        result = beans.beans_cmd(" net/bob ", "alice", bot)
        assert result == "🌟 bob has 🫘 1,000 beans! 🌟"

    def test_balance_unavailable(self, mock_requests):
        bot = make_bot()
        mock_requests.add("GET", users_url(), json=[])
        result = beans.beans_cmd("bob", "alice", bot)
        assert result == "🚫 Could not fetch bean balance for bob! 🚫"


class TestTransferBeansCmd:
    def test_non_positive_amount(self):
        bot = make_bot()
        event = MagicMock()
        match = beans.bean_add_re.match("+0 beans to bob")
        result = beans.transfer_beans_cmd(match, "alice", bot, event)
        assert result == "🚫 Amount must be positive! 🚫"

    def test_invalid_nick(self):
        bot = make_bot()
        event = MagicMock()
        event.is_nick_valid.return_value = False
        match = beans.bean_add_re.match("+5 beans to bob")
        result = beans.transfer_beans_cmd(match, "alice", bot, event)
        assert result == (
            "🚫 Invalid user! Please provide a valid IRC nickname. 🚫"
        )

    def test_self_transfer(self):
        bot = make_bot()
        event = MagicMock()
        event.is_nick_valid.return_value = True
        match = beans.bean_add_re.match("+5 beans to Alice")
        result = beans.transfer_beans_cmd(match, "alice", bot, event)
        assert result == "🤔 You can't transfer beans to yourself! 🤔"

    def test_success(self):
        bot = make_bot()
        event = MagicMock()
        event.is_nick_valid.return_value = True
        match = beans.bean_add_re.match("+5 beans to bob")
        result = beans.transfer_beans_cmd(match, "alice", bot, event)
        assert result == (
            "🔗 Transfer 🫘 5 beans to bob here: "
            + BASE_URL
            + "/transfer/alice/bob/5"
        )


class TestAdminAddBeans:
    def _match(self, text="++5 beans to bob for good work"):
        return beans.bean_admin_add_re.match(text)

    def test_no_permission(self):
        bot = make_bot()
        event = MagicMock()
        notice = MagicMock()
        has_permission = MagicMock(return_value=False)
        result = beans.admin_add_beans(
            self._match(), "alice", bot, notice, has_permission, event
        )
        assert result is None
        notice.assert_called_once_with(
            "🚫 You don't have permission to use this command! 🚫"
        )

    def test_invalid_nick(self):
        bot = make_bot()
        event = MagicMock()
        event.is_nick_valid.return_value = False
        notice = MagicMock()
        has_permission = MagicMock(return_value=True)
        result = beans.admin_add_beans(
            self._match(), "alice", bot, notice, has_permission, event
        )
        assert result == (
            "🚫 Invalid user! Please provide a valid IRC nickname. 🚫"
        )

    def test_non_positive_amount(self):
        bot = make_bot()
        event = MagicMock()
        event.is_nick_valid.return_value = True
        notice = MagicMock()
        has_permission = MagicMock(return_value=True)
        result = beans.admin_add_beans(
            self._match("++0 beans to bob for good work"),
            "alice",
            bot,
            notice,
            has_permission,
            event,
        )
        assert result == "🚫 Amount must be positive! 🚫"

    def test_harvest_creation_failure(self, mock_requests):
        bot = make_bot()
        event = MagicMock()
        event.is_nick_valid.return_value = True
        notice = MagicMock()
        has_permission = MagicMock(return_value=True)
        mock_requests.add("POST", harvests_url(), status=500)
        result = beans.admin_add_beans(
            self._match(), "alice", bot, notice, has_permission, event
        )
        assert result == "🚫 Failed to create harvest! 🚫"

    def test_assign_failure(self, mock_requests):
        bot = make_bot()
        event = MagicMock()
        event.is_nick_valid.return_value = True
        notice = MagicMock()
        has_permission = MagicMock(return_value=True)
        mock_requests.add("POST", harvests_url(), json={"id": 9})
        mock_requests.add("POST", harvests_url() + "/9/assign", status=500)
        result = beans.admin_add_beans(
            self._match(), "alice", bot, notice, has_permission, event
        )
        assert result == "⚠️ Harvest created but failed to assign to bob! ⚠️"

    def test_complete_failure(self, mock_requests):
        bot = make_bot()
        event = MagicMock()
        event.is_nick_valid.return_value = True
        notice = MagicMock()
        has_permission = MagicMock(return_value=True)
        mock_requests.add("POST", harvests_url(), json={"id": 9})
        mock_requests.add(
            "POST", harvests_url() + "/9/assign", json={"ok": True}
        )
        mock_requests.add("POST", harvests_url() + "/9/complete", status=500)
        result = beans.admin_add_beans(
            self._match(), "alice", bot, notice, has_permission, event
        )
        assert result == (
            "⚠️ Harvest assigned to bob but failed to complete! "
            "Beans not transferred yet. ⚠️"
        )

    def test_success(self, mock_requests):
        bot = make_bot()
        event = MagicMock()
        event.is_nick_valid.return_value = True
        notice = MagicMock()
        has_permission = MagicMock(return_value=True)
        mock_requests.add("POST", harvests_url(), json={"id": 9})
        mock_requests.add(
            "POST", harvests_url() + "/9/assign", json={"ok": True}
        )
        mock_requests.add(
            "POST", harvests_url() + "/9/complete", json={"ok": True}
        )
        result = beans.admin_add_beans(
            self._match(), "alice", bot, notice, has_permission, event
        )
        assert result == (
            "✨ Awarded 🫘 5 beans to bob for: good work ✨\n🔗 "
            + BASE_URL
            + "/#harvest/9"
        )


class TestTopBeans:
    def test_no_users(self, mock_requests):
        bot = make_bot()
        mock_requests.add("GET", users_url(), json=[])
        notice = MagicMock()
        message = MagicMock()
        result = beans.top_beans("", "alice", "#chan", bot, notice, message)
        assert result == "😢 No one has any beans yet! 😢"

    def test_invalid_number(self):
        bot = make_bot()
        notice = MagicMock()
        message = MagicMock()
        result = beans.top_beans(
            "notanumber", "alice", "#chan", bot, notice, message
        )
        assert result == (
            "🚫 Please provide a valid number for the top users to display. 🚫"
        )

    def test_default_top_ten(self, mock_requests):
        bot = make_bot()
        users = [{"username": f"user{i}", "bean_amount": i} for i in range(5)]
        mock_requests.add("GET", users_url(), json=users)
        notice = MagicMock()
        message = MagicMock()
        result = beans.top_beans("", "alice", "#chan", bot, notice, message)
        assert result == (
            "🏆 Top 10 Bean Holders: 1. user4 🫘 (4 beans) "
            "2. user3 🫘 (3 beans) 3. user2 🫘 (2 beans) "
            "4. user1 🫘 (1 beans) 5. user0 🫘 (0 beans)"
        )
        notice.assert_not_called()
        message.assert_not_called()

    def test_over_ten_sends_dm_in_chunks(self, mock_requests):
        bot = make_bot()
        users = [{"username": f"user{i}", "bean_amount": i} for i in range(15)]
        mock_requests.add("GET", users_url(), json=users)
        notice = MagicMock()
        message = MagicMock()
        result = beans.top_beans("15", "alice", "#chan", bot, notice, message)
        assert result is None
        notice.assert_called_once_with(
            "📩 alice, check your DM for the top 15 bean holders!"
        )

        lines = [
            f"{rank}. user{15 - rank} 🫘 ({15 - rank} beans)"
            for rank in range(1, 16)
        ]
        lines[0] = "🏆 Top 15 Bean Holders: " + lines[0]
        chunk1 = " ".join(lines[0:10])
        chunk2 = " ".join(lines[10:15])
        assert message.mock_calls == [
            call(chunk1, "alice"),
            call(chunk2, "alice"),
        ]


class TestTotalBeansCmd:
    def test_success(self, mock_requests):
        bot = make_bot()
        mock_requests.add("GET", total_url(), json={"total_beans": 4200})
        assert beans.total_beans_cmd(bot) == (
            "🌍 There are 🫘 4,200 beans in circulation! 🌍"
        )

    def test_failure(self, mock_requests):
        bot = make_bot()
        mock_requests.add("GET", total_url(), status=500)
        assert (
            beans.total_beans_cmd(bot) == "🚫 Could not fetch total beans! 🚫"
        )


class TestExportBeans:
    def test_no_users(self, mock_requests):
        bot = make_bot()
        mock_requests.add("GET", users_url(), json=[])
        assert beans.export_beans(bot) == "❌ No bean data to export."

    def test_success(self, mock_requests, patch_paste):
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[
                {"username": "bob", "bean_amount": 2},
                {"username": "alice", "bean_amount": 1},
            ],
        )
        patch_paste.return_value = "http://paste.example/abc"
        result = beans.export_beans(bot)
        assert result == (
            "📊 Bean data exported (2 users): http://paste.example/abc"
        )
        pasted_json = patch_paste.call_args.args[0]
        assert '"username": "alice"' in pasted_json
        assert pasted_json.index("alice") < pasted_json.index("bob")

    def test_paste_failure(self, mock_requests, patch_paste):
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "bob", "bean_amount": 2}],
        )
        patch_paste.side_effect = web.NoPasteException()
        result = beans.export_beans(bot)
        assert result == "❌ Failed to paste data to service."


class TestTriviaBetFunctions:
    def test_add_trivia_bet_insert_and_update(self, mock_db):
        beans.trivia_bets_table.create(mock_db.engine)
        db = mock_db.session()
        assert beans.add_trivia_bet("Creator", 1, 10, "Winner", db) is True
        row = mock_db.get_data(beans.trivia_bets_table)[0]
        assert row[:4] == ("creator", 1, 10, "winner")

        assert beans.add_trivia_bet("creator", 1, 25, "otherwin", db) is True
        rows = mock_db.get_data(beans.trivia_bets_table)
        assert len(rows) == 1
        assert rows[0][:4] == ("creator", 1, 25, "otherwin")

    def test_get_trivia_bets_filters_by_id(self, mock_db):
        beans.trivia_bets_table.create(mock_db.engine)
        db = mock_db.session()
        beans.add_trivia_bet("alice", 1, 5, "bob", db)
        beans.add_trivia_bet("carol", 2, 8, "dave", db)
        bets = beans.get_trivia_bets(1, db)
        assert len(bets) == 1
        assert bets[0]["creator"] == "alice"

    def test_get_user_bets_ordered_by_recent(self, mock_db):
        beans.trivia_bets_table.create(mock_db.engine)
        mock_db.add_row(
            beans.trivia_bets_table,
            creator="alice",
            trivia_id=1,
            bet_amount=5,
            winner="bob",
            timestamp=datetime.datetime(2020, 1, 1),
        )
        mock_db.add_row(
            beans.trivia_bets_table,
            creator="alice",
            trivia_id=2,
            bet_amount=6,
            winner="carol",
            timestamp=datetime.datetime(2020, 6, 1),
        )
        db = mock_db.session()
        bets = beans.get_user_bets("alice", db)
        assert [b["trivia_id"] for b in bets] == [2, 1]

    def test_get_recent_trivia_bets_groups_and_limits(self, mock_db):
        beans.trivia_bets_table.create(mock_db.engine)
        for trivia_id, ts in [(1, 1), (2, 2), (3, 3), (4, 4)]:
            mock_db.add_row(
                beans.trivia_bets_table,
                creator=f"user{trivia_id}",
                trivia_id=trivia_id,
                bet_amount=10,
                winner="winner",
                timestamp=datetime.datetime(2020, 1, ts),
            )
        db = mock_db.session()
        recent = beans.get_recent_trivia_bets(db)
        assert len(recent) == 3
        assert [r["trivia_id"] for r in recent] == [4, 3, 2]
        assert recent[0]["total_bet_amount"] == 10
        assert recent[0]["bet_count"] == 1

    def test_delete_trivia_bets_refunds_creators(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        beans.trivia_bets_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans(conn.nick, 100, db)
        beans.add_trivia_bet("alice", 5, 7, "bob", db)

        beans.delete_trivia_bets(5, db, conn)

        assert beans.get_beans("alice", db) == 7
        assert beans.get_beans(conn.nick, db) == 93
        assert mock_db.get_data(beans.trivia_bets_table) == []

    def test_delete_trivia_bets_prints_on_refund_failure(self, mock_db, capsys):
        beans.beans_table.create(mock_db.engine)
        beans.trivia_bets_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.add_trivia_bet("alice", 5, 7, "bob", db)

        beans.delete_trivia_bets(5, db, conn)

        assert beans.get_beans("alice", db) == 0
        captured = capsys.readouterr()
        assert "Failed to refund 7 beans to alice" in captured.out
        assert mock_db.get_data(beans.trivia_bets_table) == []

    def test_handle_trivia_win_no_bets(self, mock_db):
        beans.trivia_bets_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        assert beans.handle_trivia_win(1, "alice", db, conn) == (0, 0, [])

    def test_handle_trivia_win_no_matching_winner(self, mock_db):
        beans.trivia_bets_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.add_trivia_bet("alice", 1, 10, "bob", db)
        beans.add_trivia_bet("carol", 1, 5, "dave", db)

        result = beans.handle_trivia_win(1, "eve", db, conn)

        assert result == (0, 15, [])
        assert beans.get_trivia_bets(1, db) == []

    def test_handle_trivia_win_pays_winners_proportionally(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        beans.trivia_bets_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans(conn.nick, 1000, db)
        beans.add_trivia_bet("alice", 1, 30, "winner", db)
        beans.add_trivia_bet("carol", 1, 10, "someoneelse", db)
        beans.add_trivia_bet("dave", 1, 10, "winner", db)

        winners_count, total_bet_amount, unpaid = beans.handle_trivia_win(
            1, "Winner", db, conn
        )

        assert winners_count == 2
        assert total_bet_amount == 50
        assert unpaid == []
        assert beans.get_beans("alice", db) == 37
        assert beans.get_beans("dave", db) == 12
        assert beans.get_trivia_bets(1, db) == []

    def test_handle_trivia_win_reports_unpaid_winners(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        beans.trivia_bets_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans(conn.nick, 15, db)
        beans.add_trivia_bet("alice", 1, 10, "winner", db)
        beans.add_trivia_bet("dave", 1, 10, "winner", db)

        winners_count, total_bet_amount, unpaid = beans.handle_trivia_win(
            1, "winner", db, conn
        )

        assert winners_count == 2
        assert total_bet_amount == 20
        assert unpaid == ["dave"]
        assert beans.get_beans("alice", db) == 10
        assert beans.get_beans("dave", db) == 0


class TestTriviaFunctions:
    def test_add_and_get_trivia(self, mock_db):
        beans.trivia_table.create(mock_db.engine)
        db = mock_db.session()
        tid = beans.add_trivia("Creator", "2+2?", "Four", 50, db)
        trivia = beans.get_trivia(tid, db)
        assert trivia["creator"] == "creator"
        assert trivia["answer"] == "four"
        assert trivia["prize"] == 50

    def test_get_trivia_by_answer(self, mock_db):
        beans.trivia_table.create(mock_db.engine)
        db = mock_db.session()
        tid = beans.add_trivia("creator", "2+2?", "Four", 50, db)
        trivia = beans.get_trivia_by_answer("FOUR", db)
        assert trivia["id"] == tid
        assert beans.get_trivia_by_answer("nope", db) is None

    def test_get_latest_user_trivia(self, mock_db):
        beans.trivia_table.create(mock_db.engine)
        mock_db.add_row(
            beans.trivia_table,
            timestamp=datetime.datetime(2020, 1, 1),
            creator="alice",
            question="old",
            answer="a",
            prize=1,
        )
        mock_db.add_row(
            beans.trivia_table,
            timestamp=datetime.datetime(2020, 6, 1),
            creator="alice",
            question="new",
            answer="b",
            prize=2,
        )
        db = mock_db.session()
        trivia = beans.get_latest_user_trivia("alice", db)
        assert trivia["question"] == "new"
        assert beans.get_latest_user_trivia("bob", db) is None

    def test_get_latest_trivias_limit(self, mock_db):
        beans.trivia_table.create(mock_db.engine)
        for i in range(5):
            mock_db.add_row(
                beans.trivia_table,
                timestamp=datetime.datetime(2020, 1, i + 1),
                creator="alice",
                question=f"q{i}",
                answer="a",
                prize=1,
            )
        db = mock_db.session()
        trivias = beans.get_latest_trivias(3, db)
        assert [t["question"] for t in trivias] == ["q4", "q3", "q2"]

    def test_get_user_trivias(self, mock_db):
        beans.trivia_table.create(mock_db.engine)
        mock_db.add_row(
            beans.trivia_table,
            timestamp=datetime.datetime(2020, 1, 1),
            creator="alice",
            question="q1",
            answer="a",
            prize=1,
        )
        mock_db.add_row(
            beans.trivia_table,
            timestamp=datetime.datetime(2020, 1, 2),
            creator="bob",
            question="q2",
            answer="b",
            prize=1,
        )
        db = mock_db.session()
        trivias = beans.get_user_trivias("alice", db)
        assert len(trivias) == 1
        assert trivias[0]["question"] == "q1"

    def test_delete_trivia_refunds_bets_and_removes_row(self, mock_db):
        beans.beans_table.create(mock_db.engine)
        beans.trivia_table.create(mock_db.engine)
        beans.trivia_bets_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans(conn.nick, 100, db)
        tid = beans.add_trivia("alice", "q", "a", 10, db)
        beans.add_trivia_bet("bob", tid, 5, "someone", db)

        assert beans.delete_trivia(tid, db, conn) is True
        assert beans.get_trivia(tid, db) is None
        assert beans.get_beans("bob", db) == 5
        assert mock_db.get_data(beans.trivia_bets_table) == []

    def test_delete_trivia_missing_returns_false(self, mock_db):
        beans.trivia_table.create(mock_db.engine)
        beans.trivia_bets_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        assert beans.delete_trivia(999, db, conn) is False


def create_trivia_tables(mock_db):
    beans.beans_table.create(mock_db.engine)
    beans.trivia_table.create(mock_db.engine)
    beans.trivia_bets_table.create(mock_db.engine)


class TestTriviaCmd:
    def test_no_text_shows_help(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("", "alice", db, conn)
        assert result[0] == "🎮 Trivia Commands 🎮"

    def test_help_subcommand(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("help", "alice", db, conn)
        assert result[0] == "🎮 Trivia Commands 🎮"
        assert "- Use -> to separate your question from the answer" in result

    def test_missing_arguments(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("add", "alice", db, conn)
        assert result == (
            "❌ Missing arguments. Use '.trivia help' for usage information."
        )

    def test_add_invalid_format(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("add not the right format", "alice", db, conn)
        assert result == (
            "❌ Invalid format. Use: .trivia add <prize_amount> "
            "<question> -> <answer>"
        )

    def test_add_non_positive_prize(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd(
            "add 0 What is 2+2? -> four", "alice", db, conn
        )
        assert result == "❌ Prize must be a positive number of beans."

    def test_add_non_alnum_answer(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd(
            "add 5 What is 2+2? -> four_2", "alice", db, conn
        )
        assert result == "❌ Answer must contain only letters and numbers."

    def test_add_insufficient_beans(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 2, db)
        result = beans.trivia_cmd(
            "add 5 What is 2+2? -> four", "alice", db, conn
        )
        assert result == (
            "❌ You don't have enough beans. You have 2, but the prize is 5."
        )

    def test_add_success(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        result = beans.trivia_cmd(
            "add 5 What is 2+2? -> Four", "alice", db, conn
        )
        assert result.startswith("✅ Trivia question #") and result.endswith(
            "added with a prize of 🫘 5 beans!"
        )
        assert beans.get_beans("alice", db) == 5
        assert beans.get_beans(conn.nick, db) == 5
        trivia = beans.get_latest_user_trivia("alice", db)
        assert trivia["answer"] == "four"
        assert trivia["question"] == "What is 2+2?"

    def test_add_transfer_failure(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        with patch.object(beans, "transfer_beans", return_value=False):
            result = beans.trivia_cmd(
                "add 5 What is 2+2? -> four", "alice", db, conn
            )
        assert result == "❌ Failed to transfer beans. Please try again."

    def test_question_latest_none_found(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("question", "alice", db, conn)
        assert result == "❌ You haven't created any trivia questions yet."

    def test_question_latest_found(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        result = beans.trivia_cmd("question", "alice", db, conn)
        assert result == [
            f"📝 Trivia #{tid} (created by alice)",
            "Question: 2+2?",
            "Prize: 🫘 5 beans",
        ]

    def test_question_by_id_not_found(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("question 42", "alice", db, conn)
        assert result == "❌ Trivia question #42 not found."

    def test_question_invalid_id(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("question abc", "alice", db, conn)
        assert result == "❌ Invalid trivia ID. Please provide a number."

    def test_question_by_id_found(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        tid = beans.add_trivia("alice", "What is 2+2?", "four", 5, db)
        result = beans.trivia_cmd(f"question {tid}", "bob", db, conn)
        assert result == [
            f"📝 Trivia #{tid} (created by alice)",
            "Question: What is 2+2?",
            "Prize: 🫘 5 beans",
        ]

    def test_list_empty(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("list", "alice", db, conn)
        assert result == "❌ No trivia questions found."

    def test_list_with_data(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        result = beans.trivia_cmd("list", "bob", db, conn)
        assert result == [
            "🎯 Latest Trivia Questions 🎯",
            f'#{tid}: "2+2?" - Prize: 🫘 5 beans (by alice)',
        ]

    def test_user_no_trivias(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("user bob", "alice", db, conn)
        assert result == "❌ No trivia questions found for user bob."

    def test_user_with_trivias(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        tid = beans.add_trivia("bob", "2+2?", "four", 5, db)
        result = beans.trivia_cmd("user bob", "alice", db, conn)
        assert result == [
            "🧩 Trivia Questions by bob 🧩",
            f'#{tid}: "2+2?" - Prize: 🫘 5 beans',
        ]

    def test_delete_not_found(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("delete 5", "alice", db, conn)
        assert result == "❌ Trivia question #5 not found."

    def test_delete_invalid_id(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("delete abc", "alice", db, conn)
        assert result == "❌ Invalid trivia ID. Please provide a number."

    def test_delete_not_owner(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        result = beans.trivia_cmd(f"delete {tid}", "bob", db, conn)
        assert result == "❌ You can only delete your own trivia questions."

    def test_delete_bot_cannot_refund(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        beans.set_beans(conn.nick, 0, db)
        result = beans.trivia_cmd(f"delete {tid}", "alice", db, conn)
        assert result == (
            "❌ The bot doesn't have enough beans to refund your prize. "
            "Try again later."
        )

    def test_delete_refund_transfer_failure(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        beans.set_beans(conn.nick, 5, db)
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        with patch.object(beans, "transfer_beans", return_value=False):
            result = beans.trivia_cmd(f"delete {tid}", "alice", db, conn)
        assert result == "❌ Failed to refund beans. Please try again later."

    def test_delete_success(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        beans.set_beans(conn.nick, 5, db)
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        result = beans.trivia_cmd(f"delete {tid}", "alice", db, conn)
        assert result == (
            f"✅ Trivia question #{tid} deleted. You've been refunded "
            "🫘 5 beans."
        )
        assert beans.get_beans("alice", db) == 15
        assert beans.get_trivia(tid, db) is None

    def test_delete_fails_refunds_bot(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        beans.set_beans(conn.nick, 5, db)
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        with patch.object(beans, "delete_trivia", return_value=False):
            result = beans.trivia_cmd(f"delete {tid}", "alice", db, conn)
        assert result == (
            "❌ Failed to delete trivia question. Please try again."
        )
        assert beans.get_beans("alice", db) == 10
        assert beans.get_beans(conn.nick, db) == 5

    def test_unknown_subcommand(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        result = beans.trivia_cmd("frobnicate blah", "alice", db, conn)
        assert result == (
            "❌ Unknown subcommand: frobnicate. Use '.trivia help' for "
            "usage information."
        )


class TestTrackTriviaAnswers:
    def _match(self, answer="four"):
        return re.match(r"^\s*(\S+)\s*$", answer)

    def test_ignores_actions(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        event.type = EventType.action
        bot = make_bot()
        message = MagicMock()
        result = beans.track_trivia_answers(
            self._match(), event, db, conn, "#chan", bot, message
        )
        assert result is None
        message.assert_not_called()

    def test_ignores_non_channel(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        event.type = EventType.message
        bot = make_bot()
        message = MagicMock()
        result = beans.track_trivia_answers(
            self._match(), event, db, conn, "alice", bot, message
        )
        assert result is None
        message.assert_not_called()

    def test_no_matching_trivia(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        event.type = EventType.message
        bot = make_bot()
        message = MagicMock()
        result = beans.track_trivia_answers(
            self._match(), event, db, conn, "#chan", bot, message
        )
        assert result is None
        message.assert_not_called()

    def test_prize_send_failure(self, mock_db, mock_requests):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        beans.add_trivia("alice", "2+2?", "four", 5, db)

        event = MagicMock()
        event.type = EventType.message
        event.nick = "bob"
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 0}],
        )
        message = MagicMock()
        result = beans.track_trivia_answers(
            self._match(), event, db, conn, "#chan", bot, message
        )
        assert "doesn't have enough beans" in result

    def test_success_no_bets(self, mock_db, mock_requests):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        beans.add_trivia("alice", "2+2?", "four", 5, db)

        event = MagicMock()
        event.type = EventType.message
        event.nick = "bob"
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 500}],
        )
        mock_requests.add("POST", giftlinks_url(), json={"code": "win1"})
        message = MagicMock()
        result = beans.track_trivia_answers(
            self._match(), event, db, conn, "#chan", bot, message
        )
        assert result == [
            "🎉 bob answered correctly! The answer was 'four'. "
            "Check your DM to claim 🫘 5 beans! 🎉"
        ]
        assert beans.get_trivia_by_answer("four", db) is None

    def test_success_with_paid_bettors(self, mock_db, mock_requests):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        beans.set_beans(conn.nick, 100, db)
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        beans.add_trivia_bet("carol", tid, 20, "bob", db)

        event = MagicMock()
        event.type = EventType.message
        event.nick = "bob"
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 500}],
        )
        mock_requests.add("POST", giftlinks_url(), json={"code": "win2"})
        message = MagicMock()
        result = beans.track_trivia_answers(
            self._match(), event, db, conn, "#chan", bot, message
        )
        assert result == [
            "🎉 bob answered correctly! The answer was 'four'. "
            "Check your DM to claim 🫘 5 beans! 🎉",
            " Additionally, 1 bettors who bet on bob split a pool of "
            "🫘 20 beans!",
        ]

    def test_success_with_unpaid_bettors(self, mock_db, mock_requests):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        beans.set_beans("alice", 10, db)
        beans.set_beans(conn.nick, 0, db)
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        for name in ["c1", "c2", "c3", "c4"]:
            beans.add_trivia_bet(name, tid, 10, "bob", db)

        event = MagicMock()
        event.type = EventType.message
        event.nick = "bob"
        bot = make_bot()
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 500}],
        )
        mock_requests.add("POST", giftlinks_url(), json={"code": "win3"})
        message = MagicMock()
        result = beans.track_trivia_answers(
            self._match(), event, db, conn, "#chan", bot, message
        )
        assert result[2] == (
            " Sorry, couldn't pay 4 winners due to insufficient bot "
            "beans: c1, c2, c3 and 1 more"
        )


class TestBetCmd:
    HELP = [
        "🎲 Betting Commands 🎲",
        "> .bet trivia <trivia_id> place <amount> bean(s) on <winner> - "
        "Bet on who will win a trivia",
        "> .bet trivia list - Show recent trivias with bets",
        "> .bet trivia <trivia_id> - Show bets for a specific trivia",
        "> .bet trivia user <user> - Show bets placed by a user",
        "> .bet help - Show this help information",
        "",
        "Notes:",
        "- Winner must be a valid IRC nickname",
        "- You can't bet on trivias you created",
        "- Only one bet per trivia is allowed",
        "- If you win, you get a share of the total bet pool "
        "proportional to your bet amount",
    ]

    def test_no_text_shows_help(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd("", "alice", db, conn, event)
        assert result == self.HELP

    def test_help_subcommand(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd("help", "alice", db, conn, event)
        assert result == self.HELP

    def test_missing_arguments(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd("trivia", "alice", db, conn, event)
        assert result == (
            "❌ Missing arguments. Use '.bet help' for usage information."
        )

    def test_unknown_subcommand(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd("foo bar", "alice", db, conn, event)
        assert result == (
            "❌ Unknown subcommand: foo. Use '.bet help' for usage information."
        )

    def test_trivia_list_empty(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd("trivia list", "alice", db, conn, event)
        assert result == "❌ No active bets found."

    def test_trivia_list_skips_deleted_trivia(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()

        mock_db.add_row(
            beans.trivia_table,
            id=10,
            timestamp=datetime.datetime(2020, 1, 3),
            creator="alice",
            question="Question Ten",
            answer="a",
            prize=1,
        )
        mock_db.add_row(
            beans.trivia_table,
            id=30,
            timestamp=datetime.datetime(2020, 1, 1),
            creator="bob",
            question="Question Thirty",
            answer="a",
            prize=1,
        )
        for tid, ts, creator, amount in [
            (10, datetime.datetime(2020, 1, 3), "c1", 15),
            (10, datetime.datetime(2020, 1, 3), "c2", 15),
            (20, datetime.datetime(2020, 1, 2), "c3", 5),
            (30, datetime.datetime(2020, 1, 1), "c4", 8),
        ]:
            mock_db.add_row(
                beans.trivia_bets_table,
                creator=creator,
                trivia_id=tid,
                bet_amount=amount,
                winner="winner",
                timestamp=ts,
            )

        result = beans.bet_cmd("trivia list", "alice", db, conn, event)
        assert result == [
            "🎯 Recent Trivias with Bets 🎯",
            'Trivia #10: "Question Ten..." - 2 bets, 🫘 30 beans total',
            'Trivia #30: "Question Thirty..." - 1 bets, 🫘 8 beans total',
        ]

    def test_trivia_by_id_not_found(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd("trivia 42", "alice", db, conn, event)
        assert result == "❌ Trivia question #42 not found."

    def test_trivia_by_id_no_bets(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        result = beans.bet_cmd(f"trivia {tid}", "alice", db, conn, event)
        assert result == f"❌ No bets found for Trivia #{tid}."

    def test_trivia_by_id_with_bets_truncated(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        for i, ts in enumerate(
            [
                datetime.datetime(2020, 1, 1),
                datetime.datetime(2020, 1, 2),
                datetime.datetime(2020, 1, 3),
                datetime.datetime(2020, 1, 4),
            ]
        ):
            mock_db.add_row(
                beans.trivia_bets_table,
                creator=f"user{i}",
                trivia_id=tid,
                bet_amount=10,
                winner="winner",
                timestamp=ts,
            )

        result = beans.bet_cmd(f"trivia {tid}", "alice", db, conn, event)
        assert result == [
            f"🎯 Bets for Trivia #{tid} 🎯",
            "Question: 2+2?",
            "Total bet amount: 🫘 40 beans",
            "Recent bets:",
            "user3 bet 🫘 10 beans on winner",
            "user2 bet 🫘 10 beans on winner",
            "user1 bet 🫘 10 beans on winner",
            "...and 1 more bets",
        ]

    def test_trivia_by_id_with_few_bets_no_truncation(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        tid = beans.add_trivia("alice", "2+2?", "four", 5, db)
        beans.add_trivia_bet("bob", tid, 10, "winner", db)

        result = beans.bet_cmd(f"trivia {tid}", "alice", db, conn, event)
        assert result == [
            f"🎯 Bets for Trivia #{tid} 🎯",
            "Question: 2+2?",
            "Total bet amount: 🫘 10 beans",
            "Recent bets:",
            "bob bet 🫘 10 beans on winner",
        ]

    def test_trivia_user_no_bets(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd("trivia user bob", "alice", db, conn, event)
        assert result == "❌ No bets found for user bob."

    def test_trivia_user_with_bets_and_deleted_trivia(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        tid = beans.add_trivia("someone", "2+2?", "four", 5, db)
        mock_db.add_row(
            beans.trivia_bets_table,
            creator="bob",
            trivia_id=tid,
            bet_amount=10,
            winner="w1",
            timestamp=datetime.datetime(2020, 1, 2),
        )
        mock_db.add_row(
            beans.trivia_bets_table,
            creator="bob",
            trivia_id=999,
            bet_amount=3,
            winner="w2",
            timestamp=datetime.datetime(2020, 1, 1),
        )

        result = beans.bet_cmd("trivia user bob", "alice", db, conn, event)
        assert result == [
            "🎯 Bets placed by bob 🎯",
            "Total bet amount: 🫘 13 beans",
            "Recent bets:",
            f"Trivia #{tid}: 2+2?... - Bet 🫘 10 beans on w1",
            "Trivia #999: Unknown... - Bet 🫘 3 beans on w2",
        ]

    def test_place_bet_missing_arguments(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd(
            "trivia 1 place 5 bean on", "alice", db, conn, event
        )
        assert result == (
            "❌ Missing arguments. Use '.bet help' for usage information."
        )

    def test_place_bet_invalid_syntax(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd(
            "trivia 1 wager 5 bean on bob", "alice", db, conn, event
        )
        assert result == (
            "❌ Invalid syntax. Use '.bet trivia <trivia_id> place "
            "<amount> bean(s) on <winner>'."
        )

    def test_place_bet_non_numeric(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd(
            "trivia abc place five beans on bob", "alice", db, conn, event
        )
        assert result == "❌ Trivia ID and bet amount must be numbers."

    def test_place_bet_non_positive_amount(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        tid = beans.add_trivia("carol", "2+2?", "four", 5, db)
        result = beans.bet_cmd(
            f"trivia {tid} place 0 beans on bob", "alice", db, conn, event
        )
        assert result == "❌ Bet amount must be positive."

    def test_place_bet_trivia_not_found(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        result = beans.bet_cmd(
            "trivia 999 place 5 beans on bob", "alice", db, conn, event
        )
        assert result == "❌ Trivia question #999 not found."

    def test_place_bet_on_creator(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        tid = beans.add_trivia("carol", "2+2?", "four", 5, db)
        result = beans.bet_cmd(
            f"trivia {tid} place 5 beans on Carol", "alice", db, conn, event
        )
        assert result == "❌ You can't bet on the creator of the trivia."

    def test_place_bet_duplicate(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        beans.set_beans("alice", 100, db)
        tid = beans.add_trivia("carol", "2+2?", "four", 5, db)
        beans.add_trivia_bet("alice", tid, 5, "bob", db)
        result = beans.bet_cmd(
            f"trivia {tid} place 10 beans on dave", "alice", db, conn, event
        )
        assert result == (
            "❌ You already bet 🫘 5 beans on bob for this trivia. Only "
            "one bet per trivia is allowed."
        )

    def test_place_bet_insufficient_beans(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        beans.set_beans("alice", 2, db)
        tid = beans.add_trivia("carol", "2+2?", "four", 5, db)
        result = beans.bet_cmd(
            f"trivia {tid} place 10 beans on bob", "alice", db, conn, event
        )
        assert result == "❌ You don't have enough beans. You have 🫘 2 beans."

    def test_place_bet_transfer_failure(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        beans.set_beans("alice", 100, db)
        tid = beans.add_trivia("carol", "2+2?", "four", 5, db)
        with patch.object(beans, "transfer_beans", return_value=False):
            result = beans.bet_cmd(
                f"trivia {tid} place 10 beans on bob",
                "alice",
                db,
                conn,
                event,
            )
        assert result == "❌ Failed to transfer beans. Please try again."

    def test_place_bet_success(self, mock_db):
        create_trivia_tables(mock_db)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        event = MagicMock()
        beans.set_beans("alice", 100, db)
        tid = beans.add_trivia("carol", "2+2?", "four", 5, db)
        result = beans.bet_cmd(
            f"trivia {tid} place 10 beans on Bob", "alice", db, conn, event
        )
        assert result == (
            f"✅ You bet 🫘 10 beans on Bob to win trivia #{tid}."
        )
        assert beans.get_beans("alice", db) == 90
        assert beans.get_beans(conn.nick, db) == 10
        bets = beans.get_trivia_bets(tid, db)
        assert len(bets) == 1
        assert bets[0]["creator"] == "alice"
        assert bets[0]["winner"] == "bob"


def mock_bot_balance(mock_requests, amount):
    mock_requests.add(
        "GET",
        users_url(),
        json=[{"username": "cloudbot", "bean_amount": amount}],
    )


class TestSlots:
    def _setup(
        self, mock_db, mock_requests, bot_beans=1000, player_beans=100000
    ):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        bot = make_bot()
        if player_beans is not None:
            beans.set_beans("alice", player_beans, db)
        mock_bot_balance(mock_requests, bot_beans)
        return db, conn, bot

    def test_invalid_bet_text(self, mock_db, mock_requests):
        db, conn, bot = self._setup(mock_db, mock_requests, player_beans=None)
        result = beans.slots(
            "not-a-number",
            "alice",
            "#chan",
            MagicMock(),
            db,
            conn,
            bot,
            MagicMock(),
        )
        assert result == "Please provide a valid number for your bet."

    def test_below_min_bet(self, mock_db, mock_requests):
        db, conn, bot = self._setup(mock_db, mock_requests, player_beans=None)
        result = beans.slots(
            "1", "alice", "#chan", MagicMock(), db, conn, bot, MagicMock()
        )
        assert result == "Minimum bet is 3 beans."

    def test_bot_balance_unavailable(self, mock_db, mock_requests):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        bot = make_bot()
        mock_requests.add("GET", users_url(), json=[])
        result = beans.slots(
            "3", "alice", "#chan", MagicMock(), db, conn, bot, MagicMock()
        )
        assert result == (
            "🚫 Could not fetch bot wallet balance! Try again later. 🚫"
        )

    def test_market_share_lowers_min_bet_to_2(self, mock_db, mock_requests):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        bot = make_bot()
        beans.set_beans("otheruser", 1000, db)
        mock_bot_balance(mock_requests, 400)
        result = beans.slots(
            "1", "alice", "#chan", MagicMock(), db, conn, bot, MagicMock()
        )
        assert result == "Minimum bet is 2 beans."

    def test_market_share_lowers_min_bet_to_1(self, mock_db, mock_requests):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        bot = make_bot()
        beans.set_beans("otheruser", 1000, db)
        mock_bot_balance(mock_requests, 600)
        result = beans.slots(
            "0", "alice", "#chan", MagicMock(), db, conn, bot, MagicMock()
        )
        assert result == "Minimum bet is 1 beans."

    def test_insufficient_user_beans(self, mock_db, mock_requests):
        db, conn, bot = self._setup(mock_db, mock_requests, player_beans=None)
        result = beans.slots(
            "3", "alice", "#chan", MagicMock(), db, conn, bot, MagicMock()
        )
        assert result == (
            "You don't have enough beans to bet 3. You only have 0 beans."
        )

    def test_insufficient_bot_payout(self, mock_db, mock_requests):
        db, conn, bot = self._setup(mock_db, mock_requests, bot_beans=50)
        result = beans.slots(
            "3", "alice", "#chan", MagicMock(), db, conn, bot, MagicMock()
        )
        assert result == (
            "The bot doesn't have enough beans to pay out a potential "
            "prize of 100 beans. Try again later!"
        )

    def test_transfer_failure(self, mock_db, mock_requests):
        db, conn, bot = self._setup(mock_db, mock_requests)
        with patch.object(beans, "transfer_beans", return_value=False):
            result = beans.slots(
                "3",
                "alice",
                "#chan",
                MagicMock(),
                db,
                conn,
                bot,
                MagicMock(),
            )
        assert result == (
            "You don't have enough beans to play! You need at least 3 "
            "beans to play the slots."
        )

    def test_zero_matches(self, mock_db, mock_requests):
        db, conn, bot = self._setup(mock_db, mock_requests)
        with patch.object(
            beans.random,
            "choice",
            side_effect=["🍒", "🍋", "🍉", "🍋", "🍉", "🍒"],
        ):
            result = beans.slots(
                "3",
                "alice",
                "#chan",
                MagicMock(),
                db,
                conn,
                bot,
                MagicMock(),
            )
        assert result == (
            "🍒 🍋 | 🍋 🍉 | 🍉 🍒 Better luck next time! You lost 3 beans."
        )
        assert beans.get_beans("alice", db) == 100000 - 3

    def test_one_match(self, mock_db, mock_requests):
        db, conn, bot = self._setup(mock_db, mock_requests)
        with patch.object(
            beans.random,
            "choice",
            side_effect=["🍒", "🍋", "🍉", "🍒", "🍇", "🍊"],
        ):
            result = beans.slots(
                "3",
                "alice",
                "#chan",
                MagicMock(),
                db,
                conn,
                bot,
                MagicMock(),
            )
        assert result == (
            "🍒 🍒 | 🍋 🍇 | 🍉 🍊 Almost there! Keep trying! You lost 3 beans."
        )

    def test_two_matches_wins_prize(self, mock_db, mock_requests):
        db, conn, bot = self._setup(mock_db, mock_requests)
        mock_requests.add("POST", giftlinks_url(), json={"code": "slot2"})
        with patch.object(
            beans.random,
            "choice",
            side_effect=["🍒", "🍋", "🍉", "🍒", "🍋", "🍓"],
        ):
            result = beans.slots(
                "3",
                "alice",
                "#chan",
                MagicMock(),
                db,
                conn,
                bot,
                MagicMock(),
            )
        assert result == (
            "🍒 🍒 | 🍋 🍋 | 🍉 🍓 🎰 You won! Check your DM to claim "
            "🫘 50 beans!"
        )

    def test_two_matches_gift_link_failure(self, mock_db, mock_requests):
        db, conn, bot = self._setup(mock_db, mock_requests)
        mock_requests.add("POST", giftlinks_url(), status=500)
        with patch.object(
            beans.random,
            "choice",
            side_effect=["🍒", "🍋", "🍉", "🍒", "🍋", "🍓"],
        ):
            result = beans.slots(
                "3",
                "alice",
                "#chan",
                MagicMock(),
                db,
                conn,
                bot,
                MagicMock(),
            )
        assert result == (
            "🍒 🍒 | 🍋 🍋 | 🍉 🍓 🎰 You won! But "
            "❌ Failed to create prize gift link. Please contact an "
            "admin!"
        )

    def test_jackpot(self, mock_db, mock_requests):
        db, conn, bot = self._setup(mock_db, mock_requests)
        mock_requests.add("POST", giftlinks_url(), json={"code": "jack"})
        with patch.object(
            beans.random,
            "choice",
            side_effect=["🍒", "🍒", "🍒", "🍒", "🍒", "🍒"],
        ):
            result = beans.slots(
                "3",
                "alice",
                "#chan",
                MagicMock(),
                db,
                conn,
                bot,
                MagicMock(),
            )
        assert result == (
            "🍒 🍒 | 🍒 🍒 | 🍒 🍒 🎰 JACKPOT! Check your DM to claim "
            "🫘 100 beans!"
        )

    def test_jackpot_insufficient_bot_balance_for_prize(
        self, mock_db, mock_requests
    ):
        beans.beans_table.create(mock_db.engine)
        db = mock_db.session()
        conn = MockConn(nick="cloudbot")
        bot = make_bot()
        beans.set_beans("alice", 100000, db)
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 1000}],
        )
        mock_requests.add(
            "GET",
            users_url(),
            json=[{"username": "cloudbot", "bean_amount": 30}],
        )
        with patch.object(
            beans.random,
            "choice",
            side_effect=["🍒", "🍒", "🍒", "🍒", "🍒", "🍒"],
        ):
            result = beans.slots(
                "3",
                "alice",
                "#chan",
                MagicMock(),
                db,
                conn,
                bot,
                MagicMock(),
            )
        assert result == (
            "🍒 🍒 | 🍒 🍒 | 🍒 🍒 🎰 JACKPOT! But ❌ Bot doesn't have "
            "enough beans to award the prize of 🫘 100 beans. Please "
            "contact an admin!"
        )

    def test_cooldown_state_machine(self, mock_db, mock_requests, freeze_time):
        db, conn, bot = self._setup(mock_db, mock_requests)
        mock_requests.add("POST", giftlinks_url(), json={"code": "cd1"})

        loss_calls = ["🍒", "🍋", "🍉", "🍋", "🍉", "🍒"]
        for _ in range(3):
            with patch.object(
                beans.random, "choice", side_effect=list(loss_calls)
            ):
                result = beans.slots(
                    "",
                    "alice",
                    "#chan",
                    MagicMock(),
                    db,
                    conn,
                    bot,
                    MagicMock(),
                )
            assert result.endswith("Better luck next time! You lost 3 beans.")

        cooldown_entered = beans.slots(
            "", "alice", "#chan", MagicMock(), db, conn, bot, MagicMock()
        )
        assert cooldown_entered == (
            "⏳ You entered a cooldown! You can play again in 22.00 "
            "seconds. Increase your bet to 6 beans to play now ⏳"
        )

        still_waiting = beans.slots(
            "", "alice", "#chan", MagicMock(), db, conn, bot, MagicMock()
        )
        assert still_waiting == (
            "⏳ You need to wait 22 seconds before playing again. "
            "Increase your bet to 6 to play now. ⏳"
        )

        with patch.object(
            beans.random,
            "choice",
            side_effect=["🍒", "🍋", "🍉", "🍒", "🍋", "🍓"],
        ):
            win_during_cooldown = beans.slots(
                "6",
                "alice",
                "#chan",
                MagicMock(),
                db,
                conn,
                bot,
                MagicMock(),
            )
        assert win_during_cooldown == (
            "🍒 🍒 | 🍋 🍋 | 🍉 🍓 🎰 You won! Check your DM to claim "
            "🫘 50 beans! ⏳"
        )
