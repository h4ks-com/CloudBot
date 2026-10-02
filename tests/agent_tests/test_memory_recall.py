"""Tests for recalling the memories that bear on a request."""

from cloudbot.agent.tools.memory import (
    ensure_fts,
    recall_memories,
    store_memory,
)

FILLER = "the user said that the thing is fine"


def remember(mock_db, facts):
    ensure_fts(mock_db.engine)
    for key, value in facts.items():
        store_memory("net:mattf", key, value)


def test_a_topic_from_the_channel_brings_back_its_memory(mock_db):
    remember(
        mock_db,
        {
            "han_b_lyrics": "64 fingers sheet, sung to On the Road Again",
            "mattf_editor": "uses neovim",
        },
    )
    assert recall_memories(["net:mattf"], "make the han b song", 8) == [
        ("han_b_lyrics", "64 fingers sheet, sung to On the Road Again")
    ]


def test_words_found_in_most_memories_match_nothing(mock_db):
    remember(
        mock_db, {f"note_{index}": f"{FILLER} {index}" for index in range(25)}
    )
    assert recall_memories(["net:mattf"], "is the thing fine", 8) == []


def test_common_words_with_accents_match_nothing(mock_db):
    remember(
        mock_db,
        {
            f"nota_{index}": f"você disse que não é bom {index}"
            for index in range(25)
        },
    )
    assert recall_memories(["net:mattf"], "você não é", 8) == []


def test_weak_matches_stay_out_beside_a_strong_one(mock_db):
    remember(
        mock_db,
        {
            "creep_parody": "creep parody lyrics about code creep and pointers",
            "radio_note": "the radio played a parody once",
        },
    )
    keys = [
        key
        for key, _ in recall_memories(
            ["net:mattf"], "creep code parody pointers", 8
        )
    ]
    assert keys == ["creep_parody"]


def test_other_namespaces_are_not_searched(mock_db):
    remember(mock_db, {"han_b_lyrics": "64 fingers sheet"})
    assert recall_memories(["net:handyc"], "han b fingers", 8) == []
