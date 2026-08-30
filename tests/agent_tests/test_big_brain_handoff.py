"""Wiring tests for the think_hard escalation handoff."""

from types import SimpleNamespace

from agents import Agent

from plugins.agent import _build_big_brain_agent, _wire_big_brain


def _bot(api_key="key"):
    def get_api_key(name, default=None):
        return api_key if name == "z_ai" else default

    return SimpleNamespace(config=SimpleNamespace(get_api_key=get_api_key))


def _cfg(**overrides):
    big_brain = {
        "enabled": True,
        "model": "big-model",
        "base_url": "https://bb.example/v1",
        "api_key_config_path": "z_ai",
    }
    big_brain.update(overrides)
    return {"big_brain": big_brain}


def test_disabled_builds_nothing():
    assert (
        _build_big_brain_agent(_bot(), {"big_brain": {"enabled": False}})
        is None
    )
    assert _build_big_brain_agent(_bot(), {}) is None


def test_missing_api_key_builds_nothing():
    assert _build_big_brain_agent(_bot(api_key=None), _cfg()) is None


def test_built_agent_uses_the_configured_model():
    bb = _build_big_brain_agent(_bot(), _cfg())
    assert bb is not None
    assert bb.name == "BigBrain"
    assert bb.model.model == "big-model"


def test_wiring_is_bidirectional():
    main = Agent(name="CloudBot")
    bb = _build_big_brain_agent(_bot(), _cfg())
    _wire_big_brain(main, bb)
    assert main.handoffs[0].tool_name == "think_hard"
    assert main.handoffs[0].agent_name == "BigBrain"
    assert bb.handoffs == [main]
    assert main.handoff_description


def test_think_hard_description_tells_the_model_context_travels():
    main = Agent(name="CloudBot")
    bb = _build_big_brain_agent(_bot(), _cfg())
    _wire_big_brain(main, bb)
    assert "ENTIRE conversation" in main.handoffs[0].tool_description
