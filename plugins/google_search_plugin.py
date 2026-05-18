import random

from cloudbot import hook
from cloudbot.util import formatting, http
from plugins.ddg import search as ddg_search_impl


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


@hook.command("ddg", "g")
def ddg_search(text):
    """<query> - returns the first duckduckgo search result for <query>"""
    results = ddg_search_impl(text)
    result = results.pop()
    last_results.clear()
    last_results.extend(results)
    return f"{ result['text'] }   ---   \x02{result['url']}\x02"


@hook.command("ddg_next", "gn")
def ddg_gn(text):
    result = last_results.pop()
    if last_results:
        return f"{ result['text'] }   ---   \x02{result['url']}\x02"
    else:
        return "No search results left"
