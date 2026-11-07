from types import SimpleNamespace

from cloudbot.util.messages import ChatMessage
from plugins import altfrontend
from plugins.altfrontend import alt, rewrite


def conn(*history):
    return SimpleNamespace(
        history={"#lobby": list(history)}, config={"command_prefix": "."}
    )


def test_only_the_host_of_a_known_link_changes():
    assert (
        rewrite("look http://old.reddit.com/r/linux/comments/x?a=1 lol")
        == "look https://redlib.nadeko.net/r/linux/comments/x?a=1 lol"
    )
    assert rewrite("https://example.com/reddit.com") == (
        "https://example.com/reddit.com"
    )
    assert rewrite("https://notreddit.com/r/x") == "https://notreddit.com/r/x"


def test_a_url_argument_is_rewritten():
    assert alt(conn(), "#lobby", None, "https://x.com/jack/status/20") == (
        "https://nitter.net/jack/status/20"
    )


def test_a_reply_rewrites_the_replied_message():
    original = ChatMessage(
        "m1", "doesnm", "#lobby", "see https://youtu.be/dQw4w9WgXcQ", 0.0
    )
    assert alt(conn(), "#lobby", original) == (
        "<doesnm> see https://inv.nadeko.net/dQw4w9WgXcQ"
    )


def test_no_argument_rewrites_the_last_message_with_a_known_link():
    history = conn(
        ("bob", 0, "https://www.reddit.com/r/irc"),
        ("amy", 1, "https://example.com"),
    )
    assert (
        alt(history, "#lobby", None) == "<bob> https://redlib.nadeko.net/r/irc"
    )


def test_nothing_to_rewrite_says_how_to_use_it():
    assert alt(conn(), "#lobby", None, "https://example.com").startswith(
        "no known link"
    )


def test_every_alternative_is_a_bare_host():
    for alt_host in altfrontend.ALT_HOSTS.values():
        assert "/" not in alt_host and alt_host == alt_host.lower()


def test_a_malformed_link_is_left_alone():
    assert rewrite("http://[your-ip]:8080/ https://x.com/a") == (
        "http://[your-ip]:8080/ https://nitter.net/a"
    )


def test_a_bare_alt_skips_commands_and_quotes_actions():
    history = conn(
        ("bob", 0, "\x01ACTION look https://reddit.com/r/x\x01"),
        ("amy", 1, ".alt https://reddit.com/r/y"),
    )
    assert (
        alt(history, "#lobby", None)
        == "* bob look https://redlib.nadeko.net/r/x"
    )
