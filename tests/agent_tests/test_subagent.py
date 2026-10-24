from types import SimpleNamespace

import pytest

from cloudbot.agent import backends, subagent


def _bot():
    config = {
        "plugins": {
            "agent": {
                "enabled": True,
                "backends": {
                    "z_ai": {
                        "base_url": "https://z.example/v1",
                        "model": "glm-5.3-flash",
                        "api_key_config_path": "z_ai",
                    },
                    "claude": {
                        "base_url": "https://anthropic.example/v1",
                        "model": "claude-haiku-5-5",
                        "api_key_config_path": "claude",
                    },
                },
            }
        }
    }
    return SimpleNamespace(
        config=SimpleNamespace(get=config.get, get_api_key=lambda name: "k")
    )


@pytest.fixture
def ran(monkeypatch):
    calls = []

    async def fake_run(agent, prompt, *, run_config, **kwargs):
        calls.append(run_config)
        return SimpleNamespace(final_output="done")

    monkeypatch.setattr("cloudbot.agent.subagent.Runner.run", fake_run)
    backends._override = None
    yield calls
    backends._override = None


async def _run(bot, settings):
    return await subagent.run_subagent(
        bot,
        agent=SimpleNamespace(name="video"),
        prompt="p",
        max_turns=1,
        timeout_s=5,
        settings=settings,
    )


@pytest.mark.asyncio
async def test_a_pinned_subagent_ignores_the_active_backend(ran):
    bot = _bot()
    await _run(bot, {"backend": "claude", "model": "claude-haiku-5-5"})
    model = ran[0].model
    assert model.model == "claude-haiku-5-5"
    assert str(model._client.base_url).startswith("https://anthropic.example")


@pytest.mark.asyncio
async def test_an_unpinned_subagent_follows_the_active_backend(ran):
    bot = _bot()
    backends.set_active(bot, "claude")
    await _run(bot, {})
    assert ran[0].model == "claude-haiku-5-5"


@pytest.mark.asyncio
async def test_a_pinned_backend_that_does_not_exist_fails(ran):
    with pytest.raises(subagent.SubagentError, match="not configured"):
        await _run(_bot(), {"backend": "gpt9"})
    assert not ran
