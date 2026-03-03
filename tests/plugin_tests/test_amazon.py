from unittest.mock import MagicMock, patch

import pytest

from plugins import amazon


def make_result_html(
    asin: str = "B001234567",
    title: str = "Python Crash Course, 3rd Edition",
    price_sym: str = "$",
    price_whole: str = "23",
    price_frac: str = "99",
    rating: str = "4.8 out of 5 stars",
    review_count: str = "(1.2K)",
    prime: bool = False,
    bestseller: bool = False,
    free_shipping: bool = False,
) -> str:
    prime_html = '<i class="a-icon a-icon-prime"></i>' if prime else ""
    bestseller_html = '<span aria-label="Best Seller">Best Seller</span>' if bestseller else ""
    shipping_html = "<span>FREE Shipping</span>" if free_shipping else ""

    return f"""
    <div data-component-type="s-search-result" data-asin="{asin}">
        <h2 class="a-size-base-plus a-spacing-none a-color-base a-text-normal">
            <span>{title}</span>
        </h2>
        <span class="a-price">
            <span class="a-price-symbol">{price_sym}</span>
            <span class="a-price-whole">{price_whole}.</span>
            <span class="a-price-fraction">{price_frac}</span>
        </span>
        <span class="a-icon-alt">{rating}</span>
        <a href="/product-reviews/{asin}">{review_count}</a>
        {prime_html}
        {bestseller_html}
        {shipping_html}
    </div>
    """


def make_page(result_html: str = "") -> str:
    return f"<html><body>{result_html}</body></html>"


def _make_mock_response(page_html: str, status: int = 200) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.text = page_html
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def _run(text: str, page_html: str, parsed: bool | str = False) -> str | None:
    reply = MagicMock()
    with patch("plugins.amazon.get_session") as mock_session, patch(
        "plugins.amazon.web.try_shorten", side_effect=lambda u, **_: u
    ):
        mock_session.return_value.get.return_value = _make_mock_response(page_html)
        return amazon.amazon(text, reply, _parsed=parsed)


# --- happy path ---


def test_basic_result():
    html = make_page(make_result_html())
    result = _run("python book", html)

    assert result is not None
    assert "Python Crash Course" in result
    assert "$23.99" in result
    assert "4.8/5 stars" in result
    assert "1.2K ratings" in result
    assert "B001234567" in result


def test_result_with_prime():
    html = make_page(make_result_html(prime=True))
    result = _run("python book", html)

    assert result is not None
    assert "Prime" in result


def test_result_with_bestseller():
    html = make_page(make_result_html(bestseller=True))
    result = _run("python book", html)

    assert result is not None
    assert "Bestseller" in result


def test_result_with_free_shipping():
    html = make_page(make_result_html(free_shipping=True))
    result = _run("python book", html)

    assert result is not None
    assert "Free Shipping" in result


def test_multiple_tags():
    html = make_page(make_result_html(prime=True, bestseller=True, free_shipping=True))
    result = _run("python book", html)

    assert result is not None
    assert "Prime" in result
    assert "Bestseller" in result
    assert "Free Shipping" in result


def test_result_no_price():
    html = make_page(
        f"""
        <div data-component-type="s-search-result" data-asin="B001234567">
            <h2><span>Some Product</span></h2>
            <span class="a-icon-alt">3.5 out of 5 stars</span>
        </div>
        """
    )
    result = _run("something", html)

    assert result is not None
    assert "N/A" in result


def test_result_no_ratings():
    html = make_page(
        f"""
        <div data-component-type="s-search-result" data-asin="B001234567">
            <h2><span>Some Product</span></h2>
            <span class="a-price-whole">10.</span>
        </div>
        """
    )
    result = _run("something", html)

    assert result is not None
    assert "No Ratings" in result


def test_title_truncation():
    long_title = "A" * 100
    html = make_page(make_result_html(title=long_title))
    result = _run("something", html)

    assert result is not None
    # truncated title should not be the full 100-char string
    assert long_title not in result


def test_no_results():
    html = make_page("")
    result = _run("xyznotarealthing123456", html)

    assert result == "No results found."


def test_parsed_no_results_returns_none():
    html = make_page("")
    result = _run("B001234567", html, parsed="com")

    assert result is None


def test_parsed_result_no_url():
    html = make_page(make_result_html())
    result = _run("B001234567", html, parsed="com")

    assert result is not None
    # parsed mode omits the URL
    assert "amazon.com/dp/" not in result


def test_parsed_result_missing_title_returns_none():
    html = make_page(
        '<div data-component-type="s-search-result" data-asin="B001234567"><h2></h2></div>'
    )
    result = _run("B001234567", html, parsed="com")

    assert result is None


def test_non_parsed_result_missing_title_returns_message():
    html = make_page(
        '<div data-component-type="s-search-result" data-asin="B001234567"><h2></h2></div>'
    )
    result = _run("something", html)

    assert result == "Could not parse result."


def test_http_error_calls_reply():
    reply = MagicMock()
    with patch("plugins.amazon.get_session") as mock_session, patch(
        "plugins.amazon.web.try_shorten", side_effect=lambda u, **_: u
    ):
        mock_resp = _make_mock_response("", status=503)
        mock_resp.raise_for_status.side_effect = amazon.HTTPError(response=mock_resp)
        mock_session.return_value.get.return_value = mock_resp

        with pytest.raises(amazon.HTTPError):
            amazon.amazon("python", reply)

    reply.assert_called_once_with("Amazon API error occurred.")


# --- RATING_RE ---


@pytest.mark.parametrize(
    "text,expected",
    [
        ("4.8 out of 5 stars", "4.8"),
        ("3,5 out of 5 stars", "3.5"),
        ("4 out of 5 stars", "4"),
    ],
)
def test_rating_re(text, expected):
    m = amazon.RATING_RE.search(text)
    assert m is not None
    assert m.group(1).replace(",", ".") == expected


# --- AMAZON_RE ---
# ASINs are always exactly 10 chars: B + 9 alphanumeric


@pytest.mark.parametrize(
    "url,expected_cc,expected_asin",
    [
        ("https://www.amazon.com/dp/B001234567", "com", "B001234567"),
        ("https://www.amazon.co.uk/some-product/dp/B00ABCDE12", "co.uk", "B00ABCDE12"),
        ("https://www.amazon.de/gp/product/B00TEST123", "de", "B00TEST123"),
    ],
)
def test_amazon_re(url, expected_cc, expected_asin):
    m = amazon.AMAZON_RE.match(url)
    assert m is not None
    assert m.group(1) == expected_cc
    assert m.group(2) == expected_asin
