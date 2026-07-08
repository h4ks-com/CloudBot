"""Tests for the in-memory per-channel run log."""

import time
from dataclasses import FrozenInstanceError

import pytest
from unittest.mock import patch

from cloudbot.agent import runs
from cloudbot.agent.runs import (
    recent_block,
    recent_runs,
    record_run,
    RunRecord,
)


def _clear():
    runs._RUNS.clear()


class TestRecordAndRecall:
    def setup_method(self):
        _clear()

    def test_record_then_recall(self):
        record_run("#chan", "video", "a wave", "https://x/a.mp4")
        got = recent_runs("#chan")
        assert len(got) == 1
        assert got[0].kind == "video"
        assert got[0].url == "https://x/a.mp4"

    def test_newest_first_and_kind_filter(self):
        record_run("#chan", "video", "v1", "https://x/1.mp4")
        record_run("#chan", "song", "s1", "https://x/1.mp3")
        record_run("#chan", "video", "v2", "https://x/2.mp4")
        videos = recent_runs("#chan", "video")
        assert [r.summary for r in videos] == ["v2", "v1"]
        assert [r.summary for r in recent_runs("#chan", "song")] == ["s1"]

    def test_channels_are_isolated(self):
        record_run("#a", "video", "v", "https://x/a.mp4")
        assert recent_runs("#b") == []

    def test_no_channel_or_url_is_noop(self):
        record_run("", "video", "v", "https://x/a.mp4")
        record_run("#chan", "video", "v", "")
        assert recent_runs("#chan") == []
        assert runs._RUNS.get("") is None

    def test_detail_capped_and_kept(self):
        record_run("#chan", "song", "s", "https://x/s", detail="c" * 5000)
        (got,) = recent_runs("#chan")
        assert got.detail == "c" * runs._DETAIL_MAX

    def test_summary_capped(self):
        record_run("#chan", "video", "x" * 500, "https://x/a.mp4")
        (got,) = recent_runs("#chan")
        assert len(got.summary) == 160


class TestExpiry:
    def setup_method(self):
        _clear()

    def test_expired_entries_are_pruned(self):
        record_run("#chan", "video", "old", "https://x/old.mp4")
        with patch.object(runs, "time", wraps=time) as fake_time:
            fake_time.time.return_value = time.time() + runs._TTL_S + 1
            record_run("#chan", "video", "new", "https://x/new.mp4")
            assert [r.summary for r in recent_runs("#chan")] == ["new"]

    def test_cap_evicts_oldest(self):
        for index in range(runs._MAX_PER_CHANNEL + 5):
            record_run("#chan", "video", f"v{index}", f"https://x/{index}.mp4")
        got = recent_runs("#chan", n=runs._MAX_PER_CHANNEL)
        assert len(got) == runs._MAX_PER_CHANNEL
        assert got[0].summary == f"v{runs._MAX_PER_CHANNEL + 4}"


class TestRecentBlock:
    def setup_method(self):
        _clear()

    def test_empty_when_no_runs(self):
        assert recent_block("#chan", "video") == ""

    def test_lists_summary_and_url(self):
        record_run("#chan", "video", "a wave", "https://x/a.mp4")
        block = recent_block("#chan", "video")
        assert '"a wave"' in block
        assert "https://x/a.mp4" in block


class TestRunRecord:
    def test_frozen(self):
        record = RunRecord(kind="video", summary="s", url="u", ts=0.0)
        with pytest.raises(FrozenInstanceError):
            record.url = "other"
