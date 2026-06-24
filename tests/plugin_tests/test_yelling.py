from unittest.mock import MagicMock

from plugins import yelling


def test_yell_check():
    conn = MagicMock()

    yelling.yell_check(conn, "#yelling", "aaaaaaaaaaaaaa", "testuser")

    conn.cmd.assert_called_with(
        "KICK", "#yelling", "testuser", "USE MOAR CAPS YOU TROGLODYTE!"
    )
    conn.cmd.reset_mock()

    yelling.yell_check(conn, "#yelling", "AAAAAAAAAAAAAAAAA", "testuser")

    conn.cmd.assert_not_called()
    conn.cmd.reset_mock()

    yelling.yell_check(conn, "#yelling", "11", "testuser")

    conn.cmd.assert_not_called()
    conn.cmd.reset_mock()

    yelling.yell_check(conn, "#yelling1", "11", "testuser")

    conn.cmd.assert_not_called()
    conn.cmd.reset_mock()

    # URLs are case-sensitive, so they're stripped before the caps check.
    yelling.yell_check(
        conn, "#yelling", "http://a aaaaaaaaaaaaaaaaaaaaaa", "testuser"
    )

    conn.cmd.assert_called_with(
        "KICK", "#yelling", "testuser", "USE MOAR CAPS YOU TROGLODYTE!"
    )
    conn.cmd.reset_mock()
