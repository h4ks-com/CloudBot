import json
import random
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cloudbot import hook
from cloudbot.util import formatting, http
from plugins.ddg import search as ddg_search_impl

# Self-hosted SearXNG metasearch instance backing the .g command. Public
# engines block this server's IP directly, so search is proxied through our
# own instance instead.
SEARX_URL = "https://searx.h4ks.com"


def searx_search(query, language="en"):
    # Without language=en Bing geo-localizes by the server IP.
    params = urlencode(
        {
            "q": query,
            "format": "json",
            "categories": "general",
            "language": language,
        }
    )
    request = Request(
        f"{SEARX_URL}/search?{params}", headers={"User-Agent": "Mozilla/5.0"}
    )

    try:
        response = urlopen(request, timeout=15)
    except HTTPError:
        return []

    data = json.loads(response.read().decode("utf-8", "replace"))

    results = []
    for result in data.get("results", []):
        url = result.get("url")
        if not url:
            continue
        title = result.get("title") or url
        content = result.get("content") or ""
        results.append(
            {
                "title": title,
                "content": content,
                "url": url,
                "text": content or title,
            }
        )

    return results


def api_get(kind, query):
    """Use the RESTful Google Search API"""
    url = (
        "http://ajax.googleapis.com/ajax/services/search/%s?"
        "v=1.0&safe=moderate"
    )
    return http.get_json(url % kind, q=query)


# @hook.command("googleimage", "gis", "image")
def googleimage(text):
    """<query> - returns the first google image result for <query>"""

    parsed = api_get("images", text)
    if not 200 <= parsed["responseStatus"] < 300:
        raise OSError(
            "error searching for images: {}: {}".format(
                parsed["responseStatus"], ""
            )
        )
    if not parsed["responseData"]["results"]:
        return "no images found"
    return random.choice(parsed["responseData"]["results"][:10])["unescapedUrl"]


def google(text):
    """<query> - returns the first google search result for <query>"""

    parsed = api_get("web", text)
    if not 200 <= parsed["responseStatus"] < 300:
        raise OSError(
            "error searching for pages: {}: {}".format(
                parsed["responseStatus"], ""
            )
        )
    if not parsed["responseData"]["results"]:
        return "No fucking results found."

    result = parsed["responseData"]["results"][0]

    title = http.unescape(result["titleNoFormatting"])
    title = formatting.truncate_str(title, 60)
    content = http.unescape(result["content"])

    if not content:
        content = "No description available."
    else:
        content = http.html.fromstring(content).text_content()
        content = formatting.truncate_str(content, 150).replace("\n", "")
    return '{} -- \x02{}\x02: "{}"'.format(
        result["unescapedUrl"], title, content
    )


last_results: list[dict[str, str]] = []


@hook.command("g")
def g_search(text):
    """<query> - returns the top web search results for <query> via SearXNG"""
    results = searx_search(text)
    if not results:
        return "No results found."
    last_results.clear()
    last_results.extend(results)
    top = [last_results.pop(0) for _ in range(min(3, len(last_results)))]
    return " | ".join(f"{r['text'][:120]} \x02{r['url']}\x02" for r in top)


@hook.command("gn", "ddg_next", autohelp=False)
def g_next(text):
    """returns the next result from the last .g search"""
    if not last_results:
        return "No search results left"
    result = last_results.pop(0)
    return f"{ result['text'] }   ---   \x02{result['url']}\x02"


@hook.command("ddg")
def ddg_search(text):
    """<query> - returns the first DuckDuckGo search result for <query>"""
    results = ddg_search_impl(text)
    if not results:
        return "No results found."
    result = results[0]
    return f"{ result['text'] }   ---   \x02{result['url']}\x02"
