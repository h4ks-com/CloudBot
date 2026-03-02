"""
Integration tests for psn.py plugin.

Run with:
    uv run pytest tests/plugin_tests/test_psn.py -v -s
"""
from plugins.psn import Game, search_game


class TestSearchGame:
    def test_returns_games(self):
        results = search_game("God of War", "en-us")
        assert len(results) > 0
        assert all(isinstance(g, Game) for g in results)

    def test_first_result_is_relevant(self):
        results = search_game("God of War", "en-us")
        assert results[0].name != ""
        assert "god of war" in results[0].name.lower()

    def test_price_is_populated(self):
        results = search_game("God of War", "en-us")
        assert results[0].price != ""

    def test_url_has_no_double_slash(self):
        results = search_game("God of War", "en-us")
        for game in results:
            assert "//en-us" not in game.url, f"Double slash in URL: {game.url}"
            assert game.url.startswith("https://store.playstation.com/en-us/")

    def test_no_match_returns_featured_games(self):
        # PSN returns featured/popular games instead of empty for no-match queries
        results = search_game("xyzzy_no_such_game_12345", "en-us")
        for game in results:
            assert game.name != ""
            assert game.price != ""
            assert game.url.startswith("https://store.playstation.com/")

    def test_lang_filter(self):
        results = search_game("FIFA", "pt-br")
        assert len(results) > 0
        for game in results:
            assert "store.playstation.com/pt-br" in game.url

    def test_game_str_representation(self):
        results = search_game("God of War", "en-us")
        s = str(results[0])
        assert results[0].name in s
        assert results[0].price in s
        assert results[0].url in s
