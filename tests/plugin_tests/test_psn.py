"""
Tests for psn.py plugin.

Run with:
    uv run pytest tests/plugin_tests/test_psn.py -v -s
"""

from urllib.parse import quote

from plugins.psn import Game, search_game

SEARCH_URL = "https://store.playstation.com/{}/search/{}"


def make_tile(index: int, lang: str, slug: str, name: str, price: str) -> str:
    return f"""
    <li>
      <a href="/{lang}/product/{slug}">
        <span data-qa="search#productTile{index}#product-name">{name}</span>
        <span data-qa="search#productTile{index}#price#display-price">{price}</span>
        <span data-qa="search#productTile{index}#product-type">PS4, PS5</span>
      </a>
    </li>
    """


def make_page(*tiles: str) -> str:
    return f'<ul class="psw-grid-list psw-l-grid">{"".join(tiles)}</ul>'


class TestSearchGame:
    def test_returns_games(self, mock_requests):
        mock_requests.add(
            "GET",
            SEARCH_URL.format("en-us", quote("God of War")),
            body=make_page(
                make_tile(0, "en-us", "god-of-war", "God of War", "$19.99")
            ),
        )
        results = search_game("God of War", "en-us")
        assert len(results) > 0
        assert all(isinstance(g, Game) for g in results)

    def test_first_result_is_relevant(self, mock_requests):
        mock_requests.add(
            "GET",
            SEARCH_URL.format("en-us", quote("God of War")),
            body=make_page(
                make_tile(0, "en-us", "god-of-war", "God of War", "$19.99")
            ),
        )
        results = search_game("God of War", "en-us")
        assert results[0].name != ""
        assert "god of war" in results[0].name.lower()

    def test_price_is_populated(self, mock_requests):
        mock_requests.add(
            "GET",
            SEARCH_URL.format("en-us", quote("God of War")),
            body=make_page(
                make_tile(0, "en-us", "god-of-war", "God of War", "$19.99")
            ),
        )
        results = search_game("God of War", "en-us")
        assert results[0].price != ""

    def test_url_has_no_double_slash(self, mock_requests):
        mock_requests.add(
            "GET",
            SEARCH_URL.format("en-us", quote("God of War")),
            body=make_page(
                make_tile(0, "en-us", "god-of-war", "God of War", "$19.99")
            ),
        )
        results = search_game("God of War", "en-us")
        for game in results:
            assert "//en-us" not in game.url, f"Double slash in URL: {game.url}"
            assert game.url.startswith("https://store.playstation.com/en-us/")

    def test_no_match_returns_featured_games(self, mock_requests):
        # PSN returns featured/popular games instead of empty for no-match queries
        mock_requests.add(
            "GET",
            SEARCH_URL.format("en-us", quote("xyzzy_no_such_game_12345")),
            body=make_page(
                make_tile(0, "en-us", "featured-game", "Featured Game", "$9.99")
            ),
        )
        results = search_game("xyzzy_no_such_game_12345", "en-us")
        for game in results:
            assert game.name != ""
            assert game.price != ""
            assert game.url.startswith("https://store.playstation.com/")

    def test_lang_filter(self, mock_requests):
        mock_requests.add(
            "GET",
            SEARCH_URL.format("pt-br", quote("FIFA")),
            body=make_page(
                make_tile(0, "pt-br", "fifa-24", "FIFA 24", "R$19,99")
            ),
        )
        results = search_game("FIFA", "pt-br")
        assert len(results) > 0
        for game in results:
            assert "store.playstation.com/pt-br" in game.url

    def test_game_str_representation(self, mock_requests):
        mock_requests.add(
            "GET",
            SEARCH_URL.format("en-us", quote("God of War")),
            body=make_page(
                make_tile(0, "en-us", "god-of-war", "God of War", "$19.99")
            ),
        )
        results = search_game("God of War", "en-us")
        s = str(results[0])
        assert results[0].name in s
        assert results[0].price in s
        assert results[0].url in s
