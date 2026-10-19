import base64
from typing import Annotated, TypedDict

import requests

from cloudbot import hook
from cloudbot.bot import bot
from cloudbot.event import CommandEvent
from cloudbot.hook import Arg
from cloudbot.util.web import get_session


class VibeResponse(TypedDict, total=False):
    status: str
    message: str
    url: str
    response: str


class VibeSearchResult(TypedDict):
    project: str
    date_added: str
    date_modified: str
    num_opens: int
    html_path: str
    path_url: str
    subdomain_url: str | None
    github_url: str


class VibeClient:
    _instance = None

    def __init__(self):
        if self._instance is not None:
            return
        self._instance = self
        api_key = bot.config.get_api_key("vibegames_api_key")
        self.api_url = bot.config.get_api_key("vibegames_api_url")
        self.headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    @property
    def instance(self):
        if self._instance is None:
            self._instance = VibeClient()
        return self._instance

    def _handle_response(
        self, response: requests.Response
    ) -> VibeResponse | dict:
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            return {
                "status": "error",
                "message": str(e),
                "response": response.text,
            }

        response_json = response.json()
        if response_json["status"] != "success":
            return {
                "status": "error",
                "message": response_json["message"],
                "response": response.text,
            }

        response_data = response_json
        preferred_url = response_data.get("subdomain_url") or response_data.get(
            "path_url"
        )
        if not preferred_url and "html_path" in response_data:
            preferred_url = f"{self.api_url}{response_data['html_path']}"

        return {
            "status": "success",
            "url": preferred_url,
        }

    def create(self, name: str, prompt: str) -> VibeResponse | dict:
        """Create a new game"""
        response = get_session().post(
            f"{self.api_url}/api/ai/{name}",
            json={"content": prompt},
            headers=self.headers,
        )
        return self._handle_response(response)

    def update(self, name: str, prompt: str) -> VibeResponse | dict:
        """Update an existing game"""
        response = get_session().put(
            f"{self.api_url}/api/ai/{name}",
            json={"content": prompt},
            headers=self.headers,
        )
        return self._handle_response(response)

    def add(
        self, name: str, content: bytes, path: str = "index.html"
    ) -> VibeResponse | dict:
        """Import a game"""
        text = base64.b64encode(content).decode("utf-8")
        response = get_session().put(
            f"{self.api_url}/api/project/{name}/{path}",
            json={"content": text, "encoding": "base64"},
            headers=self.headers,
        )
        return self._handle_response(response)

    def delete(self, name: str) -> bool:
        """Delete a game"""
        response = get_session().delete(
            f"{self.api_url}/api/project/{name}", headers=self.headers
        )
        return response.status_code == 200

    def search(self, name: str) -> list[VibeSearchResult]:
        """Search for a game"""
        response = get_session().get(
            f"{self.api_url}/api/games",
            params={"search_query": name, "sort_by": "hottest"},
            headers=self.headers,
        )
        if response.status_code != 200:
            return []
        response_json = response.json()
        return response_json

    def revert(self, name: str) -> VibeResponse | dict:
        """Revert a game"""
        response = get_session().get(
            f"{self.api_url}/api/revert_project/{name}", headers=self.headers
        )
        return self._handle_response(response)


@hook.command("vibegame", "vibefind", "vibesearch")
def vibegame(
    name: Annotated[str, Arg("Game name")],
    chan: str,
    nick: str,
    event: CommandEvent,
) -> None | str:
    """Get a vibe game"""
    client = VibeClient()
    response = client.search(name)
    if not response:
        return f"Error: No game found for {name}"

    lines = []
    for result in response[:3]:
        preferred_url = result.get("subdomain_url") or result.get("path_url")
        if not preferred_url:
            preferred_url = f"{client.api_url}{result['html_path']}"
        lines.append(
            f"{result['project']} at {preferred_url} ({result['num_opens']} opens) - {result['github_url']}"
        )
    if lines:
        event.reply(*lines)
    return None


@hook.command("vibeadd", "vibecreate")
def vibe(
    name: Annotated[str, Arg("Game name")],
    prompt: Annotated[str, Arg("What the game should be")],
    chan: str,
    nick: str,
) -> str:
    """Vibe create a new game"""
    client = VibeClient()
    response = client.create(name, prompt)
    if response["status"] != "success":
        return f"Error: {response['message']} - {response['response']}"

    return f"Created {name} at {response['url']}"


@hook.command("vibeedit")
def vibe_edit(
    name: Annotated[str, Arg("Game name")],
    prompt: Annotated[str, Arg("What to change")],
    chan: str,
    nick: str,
) -> str:
    """Vibe edit a game"""
    client = VibeClient()
    response = client.update(name, prompt)
    if response["status"] != "success":
        return f"Error: {response['message']} - {response['response']}"
    return f"Updated {name} at {response['url']}"


@hook.command("vibeimport")
def vibe_import(
    name: Annotated[str, Arg("Game name, optionally followed by /file/path")],
    url: Annotated[str, Arg("URL to import from")],
    chan: str,
    nick: str,
) -> str:
    """Vibe import a game from a URL"""
    client = VibeClient()
    http_response = requests.get(url)
    if http_response.status_code != 200:
        return f"Error: {http_response.status_code} - {http_response.text}"

    # if has "/" we use it as a file path
    if "/" in name:
        name, path = name.split("/", 1)
    else:
        path = "index.html"

    content = http_response.content
    if len(content) > 10 * 1024**2:
        return "Error: File too large"

    response = client.add(name, content, path)
    if response["status"] != "success":
        return f"Error: {response['message']} - {response['response']}"
    return f"Imported {name} at {response['url']}"


@hook.command("vibedelete")
def vibe_delete(
    name: Annotated[str, Arg("Game name")], chan: str, nick: str
) -> str:
    """Vibe delete a game"""
    client = VibeClient()
    if not client.delete(name):
        return f"Error: {name} not found"
    return f"Deleted {name}"


@hook.command("viberollback", "viberevert")
def vibe_rollback(
    name: Annotated[str, Arg("Game name")], chan: str, nick: str
) -> str:
    """Vibe revert a game"""
    client = VibeClient()
    response = client.revert(name)
    if response["status"] != "success":
        return f"Error: {response['message']} - {response['response']}"
    return f"Reverted {name} at {response['url']}"
