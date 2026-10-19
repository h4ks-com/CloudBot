from unittest.mock import MagicMock, call

from cloudbot.util.command_args import build_spec
from plugins import drinks


def test_drinks_no_recipe(patch_try_shorten):
    drinks.drink_data.clear()
    drinks.drink_data.append(
        {"title": "foobar", "url": "foo.bar", "ingredients": ["foo", "bar"]}
    )

    event = MagicMock()
    text = "foo"
    chan = "#bar"
    res = drinks.drink_cmd(chan, event, text)
    assert res is None
    assert event.mock_calls == [
        call.action(
            "grabs some foo and bar\x0f and makes foo a \x02foobar\x02. foo.bar",
            "#bar",
        )
    ]


def test_drink_target_takes_rest_of_line():
    parsed = build_spec(drinks.drink_cmd).parse_text("some  nick")
    assert parsed == {"target": "some  nick"}
