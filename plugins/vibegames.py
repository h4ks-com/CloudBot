import base64
from typing import TypedDict

import requests

from cloudbot import hook
from cloudbot.bot import bot


class VibeResponse(TypedDict):
    status: str
    message: str
    url: str


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
            return self._instance
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
        from cloudbot.util.web import get_session

        response = get_session().post(
            f"{self.api_url}/api/ai/{name}",
            json={"content": prompt},
            headers=self.headers,
        )
        return self._handle_response(response)

    def update(self, name: str, prompt: str) -> VibeResponse | dict:
        """Update an existing game"""
        from cloudbot.util.web import get_session

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
        from cloudbot.util.web import get_session

        text = base64.b64encode(content).decode("utf-8")
        response = get_session().put(
            f"{self.api_url}/api/project/{name}/{path}",
            json={"content": text, "encoding": "base64"},
            headers=self.headers,
        )
        return self._handle_response(response)

    def delete(self, name: str) -> bool:
        """Delete a game"""
        from cloudbot.util.web import get_session

        response = get_session().delete(
            f"{self.api_url}/api/project/{name}", headers=self.headers
        )
        return response.status_code == 200

    def search(self, name: str) -> list[VibeSearchResult]:
        """Search for a game"""
        from cloudbot.util.web import get_session

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
        from cloudbot.util.web import get_session

        response = get_session().get(
            f"{self.api_url}/api/revert_project/{name}", headers=self.headers
        )
        return self._handle_response(response)


@hook.command("vibegame", "vibefind", "vibesearch", autohelp=False)
def vibegame(text: str, chan: str, nick: str, reply) -> None | str:
    """<name> - Get a vibe game"""
    if not text.strip():
        return "Usage: .vibegame <name>"

    name = text.strip()
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
        reply(*lines)


@hook.command("vibeadd", "vibecreate", autohelp=False)
def vibe(text: str, chan: str, nick: str) -> str:
    """<name> <prompt> - Vibe create a new game"""
    if not text.strip():
        return "Usage: .vibeadd <name> <prompt>"

    if len(text.split()) < 2:
        return "Usage: .vibeadd <name> <prompt>"

    name, prompt = text.split(maxsplit=1)
    client = VibeClient()
    response = client.create(name, prompt)
    if response["status"] != "success":
        return f"Error: {response['message']} - {response['response']}"

    return f"Created {name} at {response['url']}"


@hook.command("vibeedit", autohelp=False)
def vibe_edit(text: str, chan: str, nick: str) -> str:
    """<name> <prompt> - Vibe edit a game"""
    if not text.strip():
        return "Usage: .vibeedit <name> <prompt>"

    if len(text.split()) < 2:
        return "Usage: .vibeedit <name> <prompt>"

    name, prompt = text.split(maxsplit=1)
    client = VibeClient()
    response = client.update(name, prompt)
    if response["status"] != "success":
        return f"Error: {response['message']} - {response['response']}"
    return f"Updated {name} at {response['url']}"


@hook.command("vibeimport", autohelp=False)
def vibe_import(text: str, chan: str, nick: str) -> str:
    """<name> <url> - Vibe import a game from a URL"""
    if not text.strip() or len(text.split()) < 2:
        return "Usage: .vibeimport <name>/[file/path] <url>"

    name, url, *_ = text.split()
    client = VibeClient()
    response = requests.get(url)
    if response.status_code != 200:
        return f"Error: {response.status_code} - {response.text}"

    # if has "/" we use it as a file path
    if "/" in name:
        name, path = name.split("/", 1)
    else:
        path = "index.html"

    content = response.content
    if len(content) > 10 * 1024**2:
        return "Error: File too large"

    response = client.add(name, content, path)
    if response["status"] != "success":
        return f"Error: {response['message']} - {response['response']}"
    return f"Imported {name} at {response['url']}"


@hook.command("vibedelete", autohelp=False)
def vibe_delete(text: str, chan: str, nick: str) -> str:
    """<name> - Vibe delete a game"""
    if not text.strip():
        return "Usage: .vibedelete <name>"

    name = text.strip()
    client = VibeClient()
    if not client.delete(name):
        return f"Error: {name} not found"
    return f"Deleted {name}"


@hook.command("viberollback", "viberevert", autohelp=False)
def vibe_rollback(text: str, chan: str, nick: str) -> str:
    """<name> - Vibe revert a game"""
    if not text.strip():
        return "Usage: .viberollback <name>"

    name = text.strip()
    client = VibeClient()
    response = client.revert(name)
    if response["status"] != "success":
        return f"Error: {response['message']} - {response['response']}"
    return f"Reverted {name} at {response['url']}"
