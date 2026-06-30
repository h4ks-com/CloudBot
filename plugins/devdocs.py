# Searches for documentation in devdocs.io
# Author: Matheus Fillipe
# Date: 12/08/2022

from dataclasses import dataclass
from uuid import uuid1

from bs4 import BeautifulSoup
from fuzzywuzzy import process
from natsort import natsorted

from cloudbot import hook
from cloudbot.util.formatting import truncate_str
from cloudbot.util.web import get_session


@dataclass
class Api:
    slugs = "https://devdocs.io/docs/docs.json"
    docs = "https://documents.devdocs.io/{slug}/db.json"
    index = "https://documents.devdocs.io/{slug}/index.json"
    bodies = "https://documents.devdocs.io/{slug}/{path}.html"


SLUGS = {}


@hook.on_start()
def on_start():
    for slug in get_session().get(Api.slugs).json():
        SLUGS[slug["slug"]] = slug


@dataclass
class Doc:
    path: str
    slug: str

    def __str__(self):
        return self.url

    @property
    def url(self):
        return f"https://devdocs.io/{self.slug}/{self.path}"

    def summary(self):
        path = self.path
        if self.path.count("#"):
            path = self.path.split("#")[0]
        url = Api.bodies.format(slug=self.slug, path=path)
        soup = BeautifulSoup(get_session().get(url).text, "html.parser")
        if len(self.path.split("#")) < 2:
            node = soup.find("h1")
        else:
            id = self.path.split("#")[1]
            node = soup.find(id=id)
        header = node.text
        # Get more text after it
        return (
            header
            + " - "
            + " - ".join(n.text for n in node.find_next_siblings()[:2])
        ).strip()


def search(slug, query) -> Doc:
    url = Api.index.format(slug=slug)
    response = get_session().get(url)
    if response.status_code == 200:
        index = {
            (d["path"], uuid1()): d["name"] for d in response.json()["entries"]
        }
        _, score, (path, _) = process.extractOne(query, index)
        if score > 80:
            return Doc(path, slug)

    # loop over actual bodies
    url = Api.docs.format(slug=slug)
    response = get_session().get(url)
    if response.status_code != 200:
        return None

    data = response.json()
    bodies = {
        path: BeautifulSoup(body, "html.parser").get_text()
        for path, body in data.items()
    }
    _, _nscore, path = process.extractOne(query, bodies)

    # Find the anchor: Loop over every element that has an id and count the time that the query is found
    soup = BeautifulSoup(data[path], "html.parser")
    ids = {"": ""}
    ids_count = {}
    currid = ""
    for node in soup.select("*"):
        node_id = node.get("id")
        if node_id:
            ids_count[currid] = ids[currid].casefold().count(query.casefold())
            currid = node_id if isinstance(node_id, str) else node_id[0]
        if currid not in ids:
            ids[currid] = ""
            ids_count[currid] = 0
        ids[currid] += node.get_text()

    # Find the best match
    best = max(ids_count, key=ids_count.get)
    if ids_count[best] > 0:
        path = f"{path}#{best}"
        return Doc(path, slug)

    _, _nscore, id = process.extractOne(query, ids)
    path = f"{path}#{id}"
    return Doc(path, slug)


@hook.command("devdocs", "docs", "documentation", "doc", "api", autohelp=False)
def devdocs(text, chan, nick, reply, notice):
    """<slug> [query] - Searches for documentation on devdocs.io"""
    if not text:
        return "Please provide a slug and query."

    slug = text.lower().split(" ")[0]
    if slug == "list":
        chunks = ""
        # Limit to 500 characters
        notice("Available slugs:")
        for slug in SLUGS:
            chunks += slug + ", "
            if len(chunks) > 400:
                notice(chunks)
                chunks = ""
        return None

    if slug not in SLUGS:
        can_be = []
        if len(slug) > 1:
            for s in SLUGS:
                if s.startswith(slug):
                    can_be.append(s)
            can_be = natsorted(can_be, reverse=True)
        if can_be:
            slug = can_be[0]
        else:
            return f"Slug not found: {slug}. Use '.devdocs list' to see available slugs."

    query = " ".join(text.split()[1:])
    if not query:
        p = SLUGS[slug]
        return f"{p['name']} - {p.get('version', '')}: {', '.join(f'{k}: {v}' for k, v in p.get('links', {}).items())}"

    doc = search(slug, query)
    reply(doc.url, truncate_str(doc.summary(), 400))
    return None
