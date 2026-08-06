"""Tests for the out-of-band watcher that announces finished Kaggle runs."""

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect, text

from cloudbot.agent import kaggle_client
from cloudbot.agent.tools import kaggle
from cloudbot.util import database


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    database.metadata.bind = engine
    database.configure(engine)
    kaggle.ensure_kaggle_table(engine)
    yield engine
    database.Session.remove()
    engine.dispose()


def _record(ref, state, network="net", channel="#chan"):
    kaggle._record(
        ref,
        "a title",
        "",
        f"https://k/{ref}",
        False,
        1,
        network,
        channel,
        "nick",
    )
    if state != kaggle_client.KernelState.QUEUED.value:
        kaggle._mark_status(ref, state)


_PRE_NETWORK_SCHEMA = """
CREATE TABLE kaggle_notebooks (
    ref VARCHAR(120) PRIMARY KEY, title VARCHAR(200), description TEXT,
    url VARCHAR(300), gpu INTEGER DEFAULT '0', last_status VARCHAR(40),
    last_version INTEGER DEFAULT '0', channel VARCHAR(100), nick VARCHAR(100),
    created_at VARCHAR(32), updated_at VARCHAR(32)
)
"""


class TestMigration:
    def test_adds_network_to_a_table_written_before_it_existed(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
        with engine.begin() as conn:
            conn.execute(text(_PRE_NETWORK_SCHEMA))
            conn.execute(
                text(
                    "INSERT INTO kaggle_notebooks (ref, title) VALUES ('o/old', 'kept')"
                )
            )
        kaggle.ensure_kaggle_table(engine)
        names = {
            c["name"] for c in inspect(engine).get_columns("kaggle_notebooks")
        }
        assert "network" in names
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT title, network FROM kaggle_notebooks WHERE ref = 'o/old'"
                )
            ).fetchone()
        assert row == ("kept", None)
        engine.dispose()

    def test_is_idempotent(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'twice.db'}")
        kaggle.ensure_kaggle_table(engine)
        kaggle.ensure_kaggle_table(engine)
        engine.dispose()


class TestUnfinished:
    def test_only_non_terminal_rows(self, db):
        _record("o/running", kaggle_client.KernelState.RUNNING.value)
        _record("o/done", kaggle_client.KernelState.COMPLETE.value)
        assert [r["ref"] for r in kaggle.unfinished_notebooks()] == [
            "o/running"
        ]

    def test_stale_rows_are_dropped(self, db):
        _record("o/old", kaggle_client.KernelState.RUNNING.value)
        with kaggle._session() as session:
            session.execute(
                kaggle._NOTEBOOKS_TABLE.update().values(
                    updated_at="2000-01-01T00:00:00+00:00"
                )
            )
        assert kaggle.unfinished_notebooks() == []


class TestPoll:
    def test_announces_once_on_the_transition(self, db):
        _record("o/job", kaggle_client.KernelState.RUNNING.value)
        posted = []
        with (
            patch.object(
                kaggle.kaggle_client, "status", return_value="complete"
            ),
            patch.object(kaggle.kaggle_client, "output", return_value=([], "")),
        ):
            kaggle.poll_unfinished(
                "tok", lambda n, c, m: posted.append((n, c, m))
            )
            kaggle.poll_unfinished(
                "tok", lambda n, c, m: posted.append((n, c, m))
            )
        assert len(posted) == 1
        network, channel, message = posted[0]
        assert (network, channel) == ("net", "#chan")
        assert "complete" in message and "https://k/o/job" in message

    def test_still_running_says_nothing_but_records_the_state(self, db):
        _record("o/job", kaggle_client.KernelState.QUEUED.value)
        posted = []
        with patch.object(
            kaggle.kaggle_client, "status", return_value="running"
        ):
            kaggle.poll_unfinished("tok", lambda n, c, m: posted.append(m))
        assert posted == []
        assert kaggle.unfinished_notebooks()[0]["last_status"] == "running"

    def test_a_row_with_no_network_is_advanced_but_not_announced(self, db):
        _record("o/job", kaggle_client.KernelState.RUNNING.value, network="")
        posted = []
        with (
            patch.object(
                kaggle.kaggle_client, "status", return_value="complete"
            ),
            patch.object(kaggle.kaggle_client, "output", return_value=([], "")),
        ):
            kaggle.poll_unfinished("tok", lambda n, c, m: posted.append(m))
        assert posted == []
        assert kaggle.unfinished_notebooks() == []

    def test_a_status_error_leaves_the_row_alone(self, db):
        _record("o/job", kaggle_client.KernelState.RUNNING.value)
        posted = []
        with patch.object(
            kaggle.kaggle_client,
            "status",
            side_effect=kaggle_client.KaggleError("down"),
        ):
            kaggle.poll_unfinished("tok", lambda n, c, m: posted.append(m))
        assert posted == []
        assert kaggle.unfinished_notebooks()[0]["last_status"] == "running"

    def test_artifact_is_mirrored_into_the_line(self, db):
        _record("o/job", kaggle_client.KernelState.RUNNING.value)
        item = kaggle_client.OutputFile(
            name="out.mid", url="https://kaggle/signed"
        )
        posted = []
        with (
            patch.object(
                kaggle.kaggle_client, "status", return_value="complete"
            ),
            patch.object(
                kaggle.kaggle_client, "output", return_value=([item], "log")
            ),
            patch.object(
                kaggle,
                "_mirror_artifact",
                return_value="https://s.h4ks.com/x.mid",
            ),
        ):
            kaggle.poll_unfinished("tok", lambda n, c, m: posted.append(m))
        assert "out.mid: https://s.h4ks.com/x.mid" in posted[0]
        assert (
            "kinesthesia.h4ks.com/watch?url=https%3A%2F%2Fs.h4ks.com%2Fx.mid"
            in posted[0]
        )

    def test_the_player_link_is_named_after_the_song_not_the_notebook(self, db):
        _record("o/job", kaggle_client.KernelState.RUNNING.value)
        item = kaggle_client.OutputFile(
            name="the_fatrat_fly_away.mid", url="https://kaggle/signed"
        )
        posted = []
        with (
            patch.object(
                kaggle.kaggle_client, "status", return_value="complete"
            ),
            patch.object(
                kaggle.kaggle_client, "output", return_value=([item], "log")
            ),
            patch.object(
                kaggle,
                "_mirror_artifact",
                return_value="https://s.h4ks.com/x.mid",
            ),
        ):
            kaggle.poll_unfinished("tok", lambda n, c, m: posted.append(m))
        assert "name=the+fatrat+fly+away" in posted[0]

    def test_a_non_midi_artifact_gets_no_player_link(self, db):
        _record("o/job", kaggle_client.KernelState.RUNNING.value)
        item = kaggle_client.OutputFile(
            name="model.gguf", url="https://kaggle/signed"
        )
        posted = []
        with (
            patch.object(
                kaggle.kaggle_client, "status", return_value="complete"
            ),
            patch.object(
                kaggle.kaggle_client, "output", return_value=([item], "log")
            ),
            patch.object(
                kaggle,
                "_mirror_artifact",
                return_value="https://s.h4ks.com/x.gguf",
            ),
        ):
            kaggle.poll_unfinished("tok", lambda n, c, m: posted.append(m))
        assert "kinesthesia" not in posted[0]

    def test_a_failed_mirror_still_announces(self, db):
        _record("o/job", kaggle_client.KernelState.RUNNING.value)
        item = kaggle_client.OutputFile(
            name="out.mid", url="https://kaggle/signed"
        )
        posted = []
        with (
            patch.object(
                kaggle.kaggle_client, "status", return_value="complete"
            ),
            patch.object(
                kaggle.kaggle_client, "output", return_value=([item], "log")
            ),
            patch.object(
                kaggle, "_mirror_artifact", side_effect=OSError("boom")
            ),
        ):
            kaggle.poll_unfinished("tok", lambda n, c, m: posted.append(m))
        assert "kaggle_notebook_output" in posted[0]
