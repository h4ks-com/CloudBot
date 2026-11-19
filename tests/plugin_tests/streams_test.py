from plugins.streams import live_paths


def test_live_paths_keeps_only_ready_streams():
    listing = {
        "items": [
            {"name": "live", "ready": False},
            {"name": "weplay-mattf", "ready": True},
            {"name": "livegames", "ready": True},
        ]
    }
    assert live_paths(listing) == ["weplay-mattf", "livegames"]


def test_live_paths_is_empty_when_nothing_is_live():
    assert live_paths({"items": []}) == []
