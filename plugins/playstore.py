from bs4 import BeautifulSoup
from google_play_scraper import search
from pydantic import BaseModel, field_validator

from cloudbot import hook
from cloudbot.util import formatting
from cloudbot.util.queue import Queue


class App(BaseModel):
    appId: str
    title: str
    score: float | None = None
    genre: str
    price: float
    currency: str
    description: str
    installs: str

    @field_validator("score")
    @classmethod
    def round_score(cls, v: float | None) -> float | None:
        if v is not None:
            return round(v, 2)
        return v

    @field_validator("description")
    @classmethod
    def clean_description(cls, v: str) -> str:
        desc = v.replace("\n", " ").replace("\r", " ")
        soup = BeautifulSoup(desc, "html.parser")
        return soup.get_text()

    @property
    def url(self) -> str:
        return f"https://play.google.com/store/apps/details?id={self.appId}"

    def __str__(self) -> str:
        score_str = f"{self.score}" if self.score else "N/A"
        return f"{self.title} - {self.price}{self.currency} - \x02Score:\x02 {score_str} - \x02Genre:\x02 {self.genre} - \x02Downloads:\x02 {self.installs} - {formatting.truncate(self.description, 100)} - {self.url}"


results_queue: Queue = Queue()


def pop3(results: list[App], reply) -> str | None:
    lines = []
    exhausted = True
    for _ in range(3):
        try:
            lines.append(str(results.pop()))
        except IndexError:
            exhausted = False
            break
    if not lines:
        return "No [more] results found."
    if exhausted:
        lines.append("No [more] results found.")
    reply(*lines)
    return None


@hook.command("playstoren", "playn", autohelp=False)
def playn(text: str, chan: str, nick: str, reply) -> str | None:
    """<nick> - Returns next search result for pkg command for nick or yours by default"""
    results = results_queue[chan][nick]
    user = text.strip().split()[0] if text.strip() else ""
    if user:
        if user in results_queue[chan]:
            results = results_queue[chan][user]
        else:
            return f"Nick '{user}' has no queue."

    if len(results) == 0:
        return "No [more] results found."

    return pop3(results, reply)


@hook.command("playstore", "play", autohelp=False)
def playstore(text: str, chan: str, nick: str, reply) -> str | None:
    """<query> - Searches on playstore"""
    if not text:
        return "Please specify a search query"

    try:
        search_results = search(text)
        results = [App.model_validate(app_data) for app_data in search_results]
    except Exception as e:
        return f"Error searching Play Store: {str(e)}"

    results_queue[chan][nick] = results

    if not results:
        return "No results found."

    return pop3(results, reply)
