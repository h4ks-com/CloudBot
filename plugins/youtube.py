import logging
import re
from functools import lru_cache
from typing import Iterable, Mapping, Match, Optional, Union
from urllib.parse import parse_qs, urlparse

import isodate
import requests
from pyyoutube import Client
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.util import colors, timeformat
from cloudbot.util.formatting import pluralize_suffix, truncate
from cloudbot.util.web import get_session

youtube_re = re.compile(
    r"(?:youtube.*?(?:v=|/v/)|youtu\.be/|yooouuutuuube.*?id=)([-_a-zA-Z0-9]+)",
    re.I,
)
ytpl_re = re.compile(
    r"(.*:)//(www.youtube.com/playlist|youtube.com/playlist)(:[0-9]+)?(.*)",
    re.I,
)


base_url = "https://www.googleapis.com/youtube/v3/"


@lru_cache
def get_client() -> Client:
    api_key = bot.config.get("api_keys", {}).get("google", None)
    return Client(api_key=api_key)


def remove_tags(text):
    """Remove vtt markup tags."""
    tags = [
        r"</c>",
        r"<c(\.color\w+)?>",
        r"<\d{2}:\d{2}:\d{2}\.\d{3}>",
    ]

    for pat in tags:
        text = re.sub(pat, "", text)

    text = re.sub(
        r"(\d{2}:\d{2}):\d{2}\.\d{3} --> .* align:start position:0%",
        r"\g<1>",
        text,
    )
    # Remove HH:MM:.* lines completely
    text = re.sub(r"(\d{2}:\d{2}):\d{2}\.\d{3} --> ", "", text)
    text = re.sub(r"(\d{2}:\d{2}):\d{2}\.\d{3}", "", text)
    text = re.sub(r"^\s*\d{2}:\d{2}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s+$", "", text, flags=re.MULTILINE)
    return text


def remove_header(lines):
    """Remove vtt file header."""
    pos = -1
    for mark in (
        "##",
        "Language: en",
    ):
        if mark in lines:
            pos = lines.index(mark)
    lines = lines[pos + 1 :]
    return lines


def merge_duplicates(lines):
    """Remove duplicated subtitles.

    Duplacates are always adjacent.
    """
    last_timestamp = ""
    last_cap = ""
    for line in lines:
        if line == "":
            continue
        if re.match(r"^\d{2}:\d{2}$", line):
            if line != last_timestamp:
                yield line
                last_timestamp = line
        else:
            if line != last_cap:
                yield line
                last_cap = line


def merge_short_lines(lines):
    buffer = ""
    for line in lines:
        if line == "" or re.match(r"^\d{2}:\d{2}$", line):
            yield "\n" + line
            continue

        if len(line + buffer) < 80:
            buffer += " " + line
        else:
            yield buffer.strip()
            buffer = line
    yield buffer


def vtt2plantext(text: str) -> str:
    text = remove_tags(text)
    lines = text.splitlines()
    lines = remove_header(lines)
    lines = merge_duplicates(lines)
    lines = list(lines)
    lines = merge_short_lines(lines)
    lines = list(lines)
    return " ".join(lines)


def get_video_info(
    client: Client, video_url: str | None = None, video_id: str | None = None
) -> "dict[str, str]":
    if not video_id and not video_url:
        raise ValueError("Must provide either video URL or video ID")

    if video_url:
        video_id = get_video_id(video_url)
        if not video_id:
            return {
                "title": "Invalid URL",
                "duration": "PT0S",
                "transcript": "",
            }

    videos = client.videos.list(video_id=video_id)
    if videos is None or not videos.items:
        return {
            "title": "Title not available",
            "duration": "Duration not available",
            "transcript": "",
        }

    video = videos.items[0]
    video_info = {
        "title": video.snippet.title,
        "duration": video.contentDetails.duration,
        "transcript": "",
    }

    prefered_languages = ["en"]
    ytt_api = YouTubeTranscriptApi()
    try:
        transcripts = ytt_api.list_transcripts(video_id)
        try:
            subtitles = transcripts.find_transcript(prefered_languages).fetch()
        except NoTranscriptFound:
            manual = transcripts._manually_created_transcripts
            generated = transcripts._generated_transcripts
            choice = manual or generated
            if choice:
                transcript = choice[list(choice.keys())[0]]
                subtitles = transcript.fetch()
            else:
                subtitles = None

    except TranscriptsDisabled:
        subtitles = None
    except Exception as e:
        logging.exception(
            "Error fetching YouTube transcript for %s: %s", video_id, e
        )
        subtitles = None

    if subtitles:
        for part in subtitles.to_raw_data():
            video_info["transcript"] += part["text"] + " "

    return video_info


def search_youtube_videos(
    client: Client, query: str, max_results: int = 10
) -> "list[str]":
    video_urls = []
    search = client.search.list(q=query, max_results=max_results)
    if search is None or not search.items:
        return []
    for item in search.items:
        if item.id.kind == "youtube#video":
            video_urls.append(
                f"https://www.youtube.com/watch?v={item.id.videoId}"
            )

    return video_urls


class APIError(Exception):
    def __init__(self, message: str, response: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.response = response


class NoApiKeyError(APIError):
    def __init__(self) -> None:
        super().__init__("Missing API key")


class NoResultsError(APIError):
    def __init__(self) -> None:
        super().__init__("No results")


def raise_api_errors(response: requests.Response) -> None:
    try:
        response.raise_for_status()
    except requests.RequestException as e:
        try:
            data = response.json()
        except ValueError:
            raise e from None

        errors = data.get("errors")
        if not errors:
            errors = data.get("error", {}).get("errors")

        if not errors:
            return

        first_error = errors[0]
        domain = first_error["domain"]
        reason = first_error["reason"]
        raise APIError(f"API Error ({domain}/{reason})", data) from e


def make_short_url(video_id: str) -> str:
    return f"http://youtu.be/{video_id}"


ParamValues = Union[int, str]
ParamMap = Mapping[str, ParamValues]
Parts = Iterable[str]


def do_request(
    method: str,
    parts: Parts,
    params: Optional[ParamMap] = None,
    **kwargs: ParamValues,
) -> requests.Response:
    api_key = bot.config.get_api_key("google_dev_key")
    if not api_key:
        raise NoApiKeyError()

    if params:
        kwargs.update(params)

    kwargs["part"] = ",".join(parts)
    kwargs["key"] = api_key
    return get_session().get(base_url + method, kwargs)


def get_video(video_id: str, parts: Parts) -> requests.Response:
    return do_request("videos", parts, params={"maxResults": 1, "id": video_id})


def get_playlist(playlist_id: str, parts: Parts) -> requests.Response:
    return do_request(
        "playlists", parts, params={"maxResults": 1, "id": playlist_id}
    )


def do_search(term: str, result_type: str = "video") -> requests.Response:
    return do_request(
        "search",
        ["snippet"],
        params={"maxResults": 1, "q": term, "type": result_type},
    )


def get_video_description(video_id: str) -> str:
    parts = ["statistics", "contentDetails", "snippet"]
    request = get_video(video_id, parts)
    raise_api_errors(request)

    json = request.json()

    data = json["items"]
    if not data:
        raise NoResultsError()

    item = data[0]
    snippet = item["snippet"]
    statistics = item["statistics"]
    content_details = item["contentDetails"]

    out = "\x02{}\x02".format(snippet["title"])

    if not content_details.get("duration"):
        return out

    length = isodate.parse_duration(content_details["duration"])
    out += " - length \x02{}\x02".format(
        timeformat.format_time(int(length.total_seconds()), simple=True)
    )
    try:
        total_votes = float(statistics["likeCount"]) + float(
            statistics["dislikeCount"]
        )
    except (LookupError, ValueError):
        total_votes = 0

    if total_votes != 0:
        # format
        likes = pluralize_suffix(int(statistics["likeCount"]), "like")
        dislikes = pluralize_suffix(int(statistics["dislikeCount"]), "dislike")

        percent = 100 * float(statistics["likeCount"]) / total_votes
        out += f" - {likes}, {dislikes} (\x02{percent:.1f}\x02%)"

    if "viewCount" in statistics:
        views = int(statistics["viewCount"])
        out += " - \x02{:,}\x02 view{}".format(views, "s"[views == 1 :])

    uploader = snippet["channelTitle"]

    upload_time = isodate.parse_datetime(snippet["publishedAt"])
    out += " - \x02{}\x02 on \x02{}\x02".format(
        uploader, upload_time.strftime("%Y.%m.%d")
    )

    try:
        yt_rating = content_details["contentRating"]["ytRating"]
    except KeyError:
        pass
    else:
        if yt_rating == "ytAgeRestricted":
            out += colors.parse(" - $(red)NSFW$(reset)")

    return out


def get_video_id(url: str) -> str | None:
    # Parse the URL
    parsed_url = urlparse(url)

    # Handle different cases based on the path and query
    if parsed_url.netloc in ("www.youtube.com", "youtube.com"):
        # Check for 'v' or 'watch' paths and retrieve the video ID from the query
        if "v" in parse_qs(parsed_url.query):
            return parse_qs(parsed_url.query)["v"][0]
        elif parsed_url.path.startswith("/embed/"):
            return parsed_url.path.split("/embed/")[1]
        elif parsed_url.path.startswith("/v/"):
            return parsed_url.path.split("/v/")[1]
        elif parsed_url.path.startswith("/watch/"):
            return parse_qs(parsed_url.query)["v"][0]
        # Additional handling for other paths like /shorts/
        elif parsed_url.path.startswith("/shorts/"):
            return parsed_url.path.split("/shorts/")[1]

    elif parsed_url.netloc == "youtu.be":
        # For shortened URLs
        return parsed_url.path.lstrip("/")

    return None  # Return None if no match is found


@hook.regex(youtube_re)
def youtube_url(match: Match[str]) -> str:
    client = get_client()
    result = get_video_info(client, video_id=match.group(1))
    time = timeformat.format_time(
        int(isodate.parse_duration(result["duration"]).total_seconds()),
        simple=True,
    )
    return truncate(
        f"\x02{result['title']}\x02, \x02duration:\x02 {time} - {result['transcript']}",
        400,
    )


user_results = {}
last_youtube_url: dict[tuple[str, str], str] = {}


@hook.command("ytn")
def youtube_next(text: str, nick: str, chan: str, reply) -> str:
    global user_results, last_youtube_url
    client = get_client()
    url = user_results[nick].pop(0)
    last_youtube_url[(chan, nick)] = url
    result = get_video_info(client, video_url=url)
    time = timeformat.format_time(
        int(isodate.parse_duration(result["duration"]).total_seconds()),
        simple=True,
    )
    return truncate(
        f"{url}  -  \x02{result['title']}\x02, \x02duration:\x02 {time} - {result['transcript']}",
        400,
    )


@hook.command("youtube", "you", "yt", "y")
def youtube(text: str, nick: str, chan: str, reply) -> str:
    """<query> - Returns the first YouTube search result for <query>.

    :param text: User input
    """
    global user_results
    client = get_client()
    results = search_youtube_videos(client, text)
    user_results[nick] = results
    return youtube_next(text, nick, chan, reply)


@hook.command("youtime", "ytime")
def youtime(text: str, reply) -> str:
    """<url> - Gets the total run time of the first YouTube."""
    parts = ["statistics", "contentDetails", "snippet"]
    try:
        video_id = get_video_id(text)
        if not video_id:
            return "Invalid YouTube URL"
        request = get_video(video_id, parts)
        raise_api_errors(request)
    except NoResultsError as e:
        return e.message
    except APIError as e:
        reply(e.message)
        raise

    json = request.json()

    data = json["items"]
    item = data[0]
    snippet = item["snippet"]
    content_details = item["contentDetails"]
    statistics = item["statistics"]

    duration = content_details.get("duration")
    if not duration:
        return "Missing duration in API response"

    length = isodate.parse_duration(duration)
    l_sec = int(length.total_seconds())
    views = int(statistics["viewCount"])
    total = int(l_sec * views)

    length_text = timeformat.format_time(l_sec, simple=True)
    total_text = timeformat.format_time(total, accuracy=8)

    return (
        "The video \x02{}\x02 has a length of {} and has been viewed {:,} times for "
        "a total run time of {}!".format(
            snippet["title"], length_text, views, total_text
        )
    )


@hook.regex(ytpl_re)
def ytplaylist_url(match: Match[str]) -> str:
    location = match.group(4).split("=")[-1]
    request = get_playlist(location, ["contentDetails", "snippet"])
    raise_api_errors(request)

    json = request.json()

    data = json["items"]
    if not data:
        raise NoResultsError()

    item = data[0]
    snippet = item["snippet"]
    content_details = item["contentDetails"]

    title = snippet["title"]
    author = snippet["channelTitle"]
    num_videos = int(content_details["itemCount"])
    count_videos = " - \x02{:,}\x02 video{}".format(
        num_videos, "s"[num_videos == 1 :]
    )
    return f"\x02{title}\x02 {count_videos} - \x02{author}\x02"
