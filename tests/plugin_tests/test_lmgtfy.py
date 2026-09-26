from plugins import lmgtfy


def test_lmgtfy(patch_try_shorten):
    assert (
        lmgtfy.lmgtfy("foo bar") == "https://letmegooglethat.com/?q=foo%20bar"
    )
