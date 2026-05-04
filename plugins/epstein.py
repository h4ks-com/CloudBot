import io
from functools import lru_cache
from urllib.parse import quote

import httpx
from curl_cffi import requests
from pypdf import PdfReader

from cloudbot import hook
from cloudbot.util import formatting
from cloudbot.util.queue import Queue

SEARCH_URL = "https://www.justice.gov/multimedia-search"
HEADERS = {
    "accept": "*/*",
    "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    "sec-ch-ua-mobile": "?0",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
}

MAX_RESULTS = 20
PDF_DOWNLOAD_TIMEOUT = 10
PDF_MAX_SIZE = 10 * 1024 * 1024
PDF_MAX_TEXT_LENGTH = 100000
CONTEXT_PREVIEW_LENGTH = 350


@lru_cache
def get_queue():
    return Queue()


@lru_cache
def get_query_cache():
    """Cache to store the query per user"""
    return {}


def search_files(query: str) -> dict:
    """Search the Epstein files via justice.gov API"""
    params = {"keys": query, "page": "1"}
    response = requests.get(
        SEARCH_URL, headers=HEADERS, params=params, timeout=10
    )
    response.raise_for_status()
    return response.json()


def extract_paragraph_context(full_text: str, search_term: str) -> str | None:
    """Find search term and extract surrounding paragraph"""
    text_lower = full_text.lower()
    search_lower = search_term.lower()

    pos = text_lower.find(search_lower)
    if pos == -1:
        return None

    start = pos
    while start > 0 and full_text[start - 1 : start + 1] != "\n\n":
        start -= 1
        if pos - start > 500:
            break

    end = pos + len(search_term)
    while end < len(full_text) and full_text[end : end + 2] != "\n\n":
        end += 1
        if end - pos > 500:
            break

    paragraph = full_text[start:end].strip()
    paragraph = " ".join(paragraph.split())

    return formatting.truncate(paragraph, CONTEXT_PREVIEW_LENGTH)


async def extract_pdf_text_with_context(
    pdf_url: str, search_query: str
) -> str | None:
    """Download PDF, extract text, and find context around search query"""
    cookies = {"justiceGovAgeVerified": "true"}
    async with httpx.AsyncClient(
        headers=HEADERS,
        cookies=cookies,
        timeout=PDF_DOWNLOAD_TIMEOUT,
        follow_redirects=True,
    ) as client:
        response = await client.get(pdf_url)
        response.raise_for_status()

        if len(response.content) > PDF_MAX_SIZE:
            return None

        if not response.content.startswith(b"%PDF"):
            return None

        pdf_content = io.BytesIO(response.content)
        pdf = PdfReader(pdf_content)
        full_text = ""

        for page in pdf.pages:
            text = page.extract_text()
            full_text += text + "\n\n"
            if len(full_text) > PDF_MAX_TEXT_LENGTH:
                break

        pdf_content.close()

        return extract_paragraph_context(full_text, search_query)


def format_result(hit_data: dict) -> str:
    """Format a single search result for IRC display"""
    file_name = hit_data["ORIGIN_FILE_NAME"]
    file_url = quote(hit_data["ORIGIN_FILE_URI"], safe=":/")
    return f"\x02{file_name}\x02 :: {file_url}"


async def send_pdf_context(event, file_url: str, query: str):
    """Async helper to extract and send PDF context"""
    context = await extract_pdf_text_with_context(file_url, query)

    if context:
        event.message(f"  └─ {context}")
    else:
        event.message(
            "  └─ \x0304PDF not accessible or query not found in document\x03"
        )


@hook.command("epstein", autohelp=False)
async def epstein_search(text: str, bot, chan: str, nick: str, event):
    """<query> - Searches the Epstein files for occurrences of the query"""
    query = text.strip()
    if not query:
        return "Please provide a search query."

    try:
        data = search_files(query)
        total = data["hits"]["total"]["value"]

        if total == 0:
            return f"No results found for '{query}' in the Epstein files."

        hits = data["hits"]["hits"][:MAX_RESULTS]
        queue = get_queue()
        query_cache = get_query_cache()

        queue[chan][nick] = [hit["_source"] for hit in hits][::-1]
        query_cache[f"{chan}:{nick}"] = query

        first_hit = queue[chan][nick].pop()
        count_text = formatting.pluralize_auto(total, "occurrence")
        first_result = format_result(first_hit)

        remaining = len(queue[chan][nick])
        if remaining > 0:
            event.message(
                f"Found {count_text} of '{query}' :: {first_result} :: ({remaining} more, use .epsteinn)"
            )
        else:
            event.message(f"Found {count_text} of '{query}' :: {first_result}")

        file_url = quote(first_hit["ORIGIN_FILE_URI"], safe=":/")
        await send_pdf_context(event, file_url, query)

    except requests.exceptions.RequestException as e:
        return f"Error searching: {e}"
    except (KeyError, IndexError) as e:
        return f"Error parsing results: {e}"


@hook.command("epstein_next", "epsteinn", autohelp=False)
async def epstein_next(text: str, chan: str, nick: str, event):
    """[nick] - Gets the next result from the last Epstein search"""
    target_nick = text.strip() or nick

    queue = get_queue()
    query_cache = get_query_cache()

    try:
        results = queue[chan][target_nick]
    except KeyError:
        return (
            f"No results found for {target_nick}. Try .epstein <query> first."
        )

    if len(results) == 0:
        return f"No more results for {target_nick}."

    next_result = results.pop()
    formatted = format_result(next_result)

    remaining = len(results)
    if remaining > 0:
        event.message(f"{formatted} :: ({remaining} more remaining)")
    else:
        event.message(formatted)

    query = query_cache.get(f"{chan}:{target_nick}", "")
    if query:
        file_url = quote(next_result["ORIGIN_FILE_URI"], safe=":/")
        await send_pdf_context(event, file_url, query)
