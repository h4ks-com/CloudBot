"""Picking the agent backend: order, admin override, per-backend capabilities."""

from types import SimpleNamespace

import pytest

from cloudbot.agent import backends


def _bot(**extra):
    config = {
        "plugins": {
            "agent": {
                "backends": {
                    "z_ai": {
                        "base_url": "https://z.example/v1",
                        "model": "glm-5.2",
                        "big_brain": {
                            "enabled": True,
                            "base_url": "https://z.example/v1",
                            "model": "glm-5.1",
                            "api_key_config_path": "z_ai",
                        },
                    },
                    "claude": {
                        "base_url": "https://anthropic.example/v1",
                        "model": "claude-haiku-5-5",
                        "multimodal": True,
                    },
                    "openrouter": {
                        "base_url": "https://or.example/v1",
                        "model": "air",
                    },
                    **extra,
                }
            }
        }
    }
    return SimpleNamespace(config=SimpleNamespace(get=config.get))


@pytest.fixture(autouse=True)
def _clear_override():
    backends._override = None
    yield
    backends._override = None


def test_first_backend_is_the_default():
    bot = _bot()
    assert backends.active_name(bot) == "z_ai"
    assert backends.order(bot) == ["z_ai", "claude", "openrouter"]


def test_override_leads_and_the_rest_stay_as_fallbacks():
    bot = _bot()
    assert backends.set_active(bot, "claude")
    assert backends.active_name(bot) == "claude"
    assert backends.order(bot) == ["claude", "z_ai", "openrouter"]


def test_unknown_backend_is_refused_and_changes_nothing():
    bot = _bot()
    assert not backends.set_active(bot, "gpt9")
    assert backends.active_name(bot) == "z_ai"


def test_capabilities_follow_the_active_backend():
    bot = _bot()
    assert not backends.reads_images(bot)
    assert backends.escalation(bot).is_usable()

    backends.set_active(bot, "claude")
    assert backends.reads_images(bot)
    assert not backends.escalation(bot).is_usable()


def test_an_escalation_missing_its_endpoint_is_not_usable():
    bot = _bot(
        half={
            "base_url": "https://half.example/v1",
            "model": "m",
            "big_brain": {"enabled": True, "model": "stronger"},
        }
    )
    backends.set_active(bot, "half")
    assert not backends.escalation(bot).is_usable()


def test_a_misconfigured_backend_is_skipped_not_fatal():
    bot = _bot(broken={"model": "no base url"})
    assert "broken" not in backends.names(bot)
    assert backends.active_name(bot) == "z_ai"


def test_no_backends_configured_is_survivable():
    bot = SimpleNamespace(config=SimpleNamespace(get=lambda *a, **k: None))
    assert backends.active_name(bot) == ""
    assert backends.order(bot) == []
    assert backends.active(bot) is None
    assert not backends.reads_images(bot)
    assert not backends.escalation(bot).is_usable()
