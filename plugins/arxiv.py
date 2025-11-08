import io
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import feedparser
import requests
import validators
from pypdf import PdfReader

from cloudbot import hook
from cloudbot.util import formatting
from cloudbot.util.http import ua_firefox
from cloudbot.util.web import get_session
from plugins.huggingface import FileIrcResponseWrapper

API_URL = "https://export.arxiv.org/api/query"
MAX_RESULTS = 3
PDF_VIEW_URL = "https://arxiv.org/pdf/"
PDF_REQUEST_TIMEOUT = 10  # seconds
PDF_MAX_CONTENT_LENGTH = 100000  # characters
MAX_PDF_SIZE = 1024 * 1024 * 10  # 10MB


def upload_responses(text_contents: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".txt") as f:
        with open(f.name, "wb") as file:
            file.write(text_contents.encode("utf-8"))
        image_url = FileIrcResponseWrapper.upload_file(f.name, "st")
    return image_url


class ApiError(Exception):
    pass


@dataclass
class SearchResult:
    title: str
    authors: List[str]
    summary: str
    link: str
    published: Optional[str] = None


@dataclass
class UserPage:
    query: str
    start: int
    sort_by_date: bool = False


def parse_arxiv_xml(xml_text: str) -> List[SearchResult]:
    feed = feedparser.parse(xml_text)
    results = []
    for entry in feed.entries:
        title = entry.title
        authors = [author.name for author in entry.authors]
        summary = entry.summary
        link = entry.link
        published = entry.published
        result = SearchResult(
            title=title,
            authors=authors,
            summary=summary,
            link=link,
            published=published,
        )
        results.append(result)
    return results


def search_arxiv(
    page: UserPage, max_results=10, sort_by_date: bool = False
) -> List[SearchResult]:
    query = page.query
    start = page.start
    params = {
        "search_query": f"all:{query.casefold()}",
        "sortBy": "relevance" if not sort_by_date else "submittedDate",
        "start": start,
        "max_results": max_results,
        "SortOrder": "descending",
    }
    response = get_session().get(API_URL, params=params)
    if response.status_code == 200:
        data = response.text
        return parse_arxiv_xml(data)
    raise ApiError(response.text)


def format_response(start: int, results: List[SearchResult]) -> List[str]:
    response = []
    for i, result in enumerate(results):
        parts = ""
        parts += formatting.truncate(
            f"\x02{start + i + 1})\x02 {result.title}", 120
        )
        if result.published:
            parts += f" {result.published}"
        parts += formatting.truncate(
            f" \x02Authors:\x02 {', '.join(result.authors)}.", 80
        )
        parts += formatting.truncate(f" {result.summary}", 240)
        parts = formatting.truncate(parts, 400)
        parts += f" :: {result.link}"
        response.append(parts)

    return response


user_pages: Dict[str, UserPage] = {}
displayed_results: Dict[str, List[SearchResult]] = {}


@hook.command("arxiv", "ax")
def arxiv(text: str, nick: str):
    """<query> - Search arxiv.org for articles matching <query>. Can sort by date if query starts with '-t'."""
    global user_pages
    query = text.strip()
    if not query:
        return "Please provide a query"

    sort_by_date = False
    if query.startswith("-t"):
        sort_by_date = True
        query = query[2:].strip()

    user_pages[nick] = UserPage(query=query, start=0)
    results = search_arxiv(
        user_pages[nick], max_results=MAX_RESULTS, sort_by_date=sort_by_date
    )
    displayed_results[nick] = results
    return format_response(0, results)


@hook.command("arxiv_next", "axn", autohelp=False)
def arxiv_next(text: str, nick: str):
    """Show next page of results"""
    global user_pages
    if text.strip():
        nick = text.strip()

    page = user_pages.get(nick)
    if not page:
        return f"No active search for {nick}"

    page.start += MAX_RESULTS
    results = search_arxiv(
        page, max_results=MAX_RESULTS, sort_by_date=page.sort_by_date
    )
    displayed_results[nick] = results
    return format_response(page.start, results)


@hook.command("axsummarize", "axsummary", "axs", autohelp=False)
def summarize_command(
    bot, reply, text: str, chan: str, nick: str, conn
) -> str | List[str] | None:
    """Summarizes the contents of the article"""
    global displayed_results
    api_key = bot.config.get_api_key("huggingface")
    if not api_key:
        return "error: missing api key for huggingface"

    from plugins.gpt import summarize

    article = text.strip()

    if article.isdigit() and int(article) > 0:
        results = displayed_results.get(nick)
        if not results:
            return "No active search for you. Try .ax <query> first"
        if len(results) < int(article):
            return f"Cannot pick article {article} because there are only {len(results)} results"
        article_url = results[int(article) - 1].link
    else:
        article_url = article

    # Validate url
    if not validators.url(article_url):
        return "Invalid URL: " + article_url

    article_id = article_url.split("/")[-1]
    article_pdf_url = f"{PDF_VIEW_URL}{article_id}"

    try:
        response = get_session().get(
            article_pdf_url,
            headers={"User-Agent": ua_firefox},
            timeout=PDF_REQUEST_TIMEOUT,
            stream=True,
        )
        if response.status_code != 200:
            return "Error fetching article"
        if int(response.headers.get("Content-Length", 0)) > MAX_PDF_SIZE:
            return "Article is too large to process"
        size = 0
        start = time.time()

        body_bytes = io.BytesIO()
        for chunk in response.iter_content(1024):
            if time.time() - start > PDF_REQUEST_TIMEOUT:
                return "Timeout fetching article"

            size += len(chunk)
            if size > MAX_PDF_SIZE:
                return "Article is too large to process"
            body_bytes.write(chunk)

        article_text = ""
        body_bytes.seek(0)
        pdf = PdfReader(body_bytes)
        for page in pdf.pages:
            text = page.extract_text()
            article_text += text
            if len(article_text) > PDF_MAX_CONTENT_LENGTH:
                break
            break
        body_bytes.close()

    except requests.exceptions.RequestException as e:
        return f"Timeout fetching article: {e}"

    # Get all as text but clamp at 10000 characters
    article_text = formatting.truncate(article_text, PDF_MAX_CONTENT_LENGTH)
    return summarize(
        [article_text],
        text.strip() == "image",
        nick,
        chan,
        bot,
        reply,
        "article",
    )
