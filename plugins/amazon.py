import re

from bs4 import BeautifulSoup
from bs4.element import Tag
from requests import HTTPError

from cloudbot import hook
from cloudbot.util import colors, formatting, web
from cloudbot.util.web import get_session

SEARCH_URL = "http://www.amazon.{}/s/"
REGION = "com"

AMAZON_RE = re.compile(
    r""".*ama?zo?n\.(com|co\.uk|com\.au|de|fr|ca|cn|es|it)/.*/(?:exec/obidos/ASIN/|o/|gp/product/|(?:(?:[^"'/]*)/)?dp/|)(B[A-Z0-9]{9})""",
    re.I,
)

# Feel free to set this to None or change it to your own ID.
# Or leave it in to support CloudBot, it's up to you!
# requsted to remove it by network
AFFILIATE_TAG = ""

FREE_SHIPPING_RE = re.compile(
    r"(Kostenlose Lieferung|Livraison gratuite|FREE Shipping|Envío GRATIS|Spedizione gratuita)",
    re.I,
)

RATING_RE = re.compile(r"([0-9]+(?:[.,][0-9])?) out of")


@hook.regex(AMAZON_RE)
def amazon_url(match, reply):
    cc = match.group(1)
    asin = match.group(2)
    return amazon(asin, reply, _parsed=cc)


@hook.command("amazon", "az")
def amazon(text, reply, _parsed: bool | str = False):
    """<query> -- Searches Amazon for query"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, "
        "like Gecko) Chrome/41.0.2228.0 Safari/537.36",
        "Referer": "http://www.amazon.com/",
    }
    params = {"url": "search-alias", "field-keywords": text.strip()}
    if _parsed:
        request = get_session().get(
            SEARCH_URL.format(_parsed), params=params, headers=headers
        )
    else:
        request = get_session().get(
            SEARCH_URL.format(REGION), params=params, headers=headers
        )

    try:
        request.raise_for_status()
    except HTTPError:
        reply("Amazon API error occurred.")
        raise

    soup = BeautifulSoup(request.text, "lxml")

    results = soup.find_all(
        "div", attrs={"data-component-type": "s-search-result"}
    )
    if not results:
        if not _parsed:
            return "No results found."
        return None

    item = results[0]
    if not isinstance(item, Tag):
        if not _parsed:
            return "Could not parse result."
        return None
    asin = item.get("data-asin", "")
    if not isinstance(asin, str):
        asin = ""

    h2 = item.find("h2")
    title_span = h2.find("span") if isinstance(h2, Tag) else None
    if not isinstance(title_span, Tag):
        if not _parsed:
            return "Could not parse result."
        return None

    title = formatting.truncate(title_span.get_text(strip=True), 60)

    tags: list[str] = []
    if item.find("i", class_="a-icon-prime"):
        tags.append("$(b)Prime$(b)")
    if item.find("span", attrs={"aria-label": "Best Seller"}):
        tags.append("$(b)Bestseller$(b)")
    if FREE_SHIPPING_RE.search(item.get_text()):
        tags.append("$(b)Free Shipping$(b)")

    price_sym = item.find("span", class_="a-price-symbol")
    price_whole = item.find("span", class_="a-price-whole")
    price_frac = item.find("span", class_="a-price-fraction")
    if isinstance(price_whole, Tag):
        sym = (
            price_sym.get_text(strip=True) if isinstance(price_sym, Tag) else ""
        )
        whole = price_whole.get_text(strip=True).rstrip(".")
        frac = (
            price_frac.get_text(strip=True)
            if isinstance(price_frac, Tag)
            else "00"
        )
        price = f"{sym}{whole}.{frac}"
    else:
        price = "N/A"

    # span.a-icon-alt contains text like "4.8 out of 5 stars"
    rating_str = "No Ratings"
    rating_span = item.find("span", class_="a-icon-alt")
    if isinstance(rating_span, Tag):
        m = RATING_RE.search(rating_span.get_text(strip=True))
        if m:
            rating = m.group(1).replace(",", ".")
            review_link = item.find(
                "a", href=re.compile(r"(customerReviews|product-reviews)")
            )
            # Amazon wraps the count in parens, e.g. "(2.1K)"
            count = (
                review_link.get_text(strip=True).strip("()")
                if isinstance(review_link, Tag)
                else ""
            )
            rating_str = (
                f"{rating}/5 stars ({count} ratings)"
                if count
                else f"{rating}/5 stars"
            )

    if AFFILIATE_TAG:
        url = f"http://www.amazon.com/dp/{asin}/?tag={AFFILIATE_TAG}"
    else:
        url = f"http://www.amazon.com/dp/{asin}/"
    url = web.try_shorten(url)

    tag_str = " - " + ", ".join(tags) if tags else ""

    if not _parsed:
        return colors.parse(
            "".join(
                f"$(b){title}$(b) ({price}) - {rating_str}{tag_str} - {url}".splitlines()
            )
        )
    else:
        return colors.parse(
            "".join(
                f"$(b){title}$(b) ({price}) - {rating_str}{tag_str}".splitlines()
            )
        )
