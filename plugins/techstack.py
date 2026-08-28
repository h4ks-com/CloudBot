"""
.techstack - fingerprint a website's tech stack from response headers and HTML
"""

import re
import urllib.parse

import requests

from cloudbot import hook
from cloudbot.util.web import get_session

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# substring (lowercased) -> label; scanned against all header values
SERVER_HINTS = [
    ("cloudflare", "Cloudflare CDN"),
    ("vercel", "Vercel"),
    ("netlify", "Netlify"),
    ("ghs", "GitHub Pages"),
    ("caddy", "Caddy"),
    ("envoy", "Envoy proxy"),
    ("kestrel", "ASP.NET/Kestrel"),
    ("gunicorn", "Gunicorn (Python)"),
    ("uvicorn", "Uvicorn (Python)"),
    ("werkzeug", "Werkzeug (Flask)"),
    ("openresty", "OpenResty (nginx+lua)"),
    ("litespeed", "LiteSpeed"),
    ("apache", "Apache"),
    ("nginx", "nginx"),
    ("iis", "IIS"),
    ("varnish", "Varnish"),
]

# Set-Cookie name (lowercased) -> label
COOKIE_HINTS = [
    ("phpsessid", "PHP"),
    ("csrftoken", "Django"),
    ("django_", "Django"),
    ("connect.sid", "Express (Node)"),
    ("jsessionid", "Java Servlet"),
    ("laravel_session", "Laravel"),
    ("rack.session", "Ruby Rack"),
    ("asp.net_sessionid", "ASP.NET"),
    ("awsalb", "AWS ALB"),
    ("__cf_bm", "Cloudflare"),
    ("cf_clearance", "Cloudflare"),
    ("shopify", "Shopify"),
]

# <meta name="generator"> content / X-Generator values
GENERATOR_HINTS = [
    ("wordpress", "WordPress"),
    ("drupal", "Drupal"),
    ("joomla", "Joomla"),
    ("ghost", "Ghost"),
    ("wix.com", "Wix"),
    ("squarespace", "Squarespace"),
    ("shopify", "Shopify"),
    ("hugo", "Hugo"),
    ("jekyll", "Jekyll"),
    ("gatsby", "Gatsby"),
    ("next.js", "Next.js"),
    ("nuxt", "Nuxt"),
    ("docusaurus", "Docusaurus"),
    ("discourse", "Discourse"),
    ("mediawiki", "MediaWiki"),
    ("owncast", "Owncast"),
    ("gravcms", "Grav"),
]

# regexes against the HTML body
HTML_HINTS = [
    (r"wp-content|wp-includes|/wp-json/", "WordPress"),
    (r"__NEXT_DATA__|/_next/static/", "Next.js (React)"),
    (r"__NUXT__|/_nuxt/", "Nuxt (Vue)"),
    (r"data-reactroot|react-dom", "React"),
    (r"data-v-[0-9a-f]{6,}", "Vue.js"),
    (r"ng-version=", "Angular"),
    (r"_app/immutable", "SvelteKit"),
    (r"cdn\.tailwindcss", "Tailwind CSS"),
    (r"bootstrap(\.min)?\.(css|js)", "Bootstrap"),
    (r"jquery(-|\.)\d|/jquery", "jQuery"),
    (r"htmx(\.min)?\.js", "htmx"),
    (r"alpine(\.min)?\.js", "Alpine.js"),
    (r"cdn\.shopify|/cdn/shop/", "Shopify"),
    (r"googletagmanager", "Google Tag Manager"),
    (r"google-analytics|gtag\(", "Google Analytics"),
    (r"plausible\.io", "Plausible Analytics"),
    (r"matomo|piwik\.js", "Matomo"),
    (r"assets/index-[0-9a-f]{8,}\.(js|css)|/vite/", "Vite build"),
]

MAX_BODY = 512 * 1024  # read at most 512KiB of HTML


def _scan(hints, text, found):
    for needle, label in hints:
        if label not in found and needle in text:
            found.append(label)


def _scan_re(hints, text, found):
    for pattern, label in hints:
        if label not in found and re.search(pattern, text, re.I):
            found.append(label)


@hook.command()
def stack(text):
    """<url> - fingerprint a website's tech stack from headers and HTML"""
    url = text.strip()
    if "://" not in url:
        url = "http://" + url

    try:
        r = get_session().get(
            url,
            timeout=10,
            headers={"User-Agent": UA},
            allow_redirects=True,
            stream=True,
        )
    except requests.exceptions.RequestException:
        return "Couldn't reach {}.".format(url)

    try:
        try:
            body = next(r.iter_content(chunk_size=MAX_BODY)).decode(
                "utf-8", "replace"
            )
        except StopIteration:
            body = ""
    finally:
        r.close()

    found = []

    # 1. headers: server, cdn/proxy hints across all values
    header_text = " ".join(str(v) for v in r.headers.values()).lower()
    _scan(SERVER_HINTS, header_text, found)

    server = (r.headers.get("Server") or "").strip()
    powered = (r.headers.get("X-Powered-By") or "").strip()

    # 2. cookies -> backend framework
    cookies = (r.headers.get("Set-Cookie") or "").lower()
    _scan(COOKIE_HINTS, cookies, found)

    # 3. generator meta / X-Generator header
    generator_text = (r.headers.get("X-Generator") or "") + " " + body[:4096]
    _scan(GENERATOR_HINTS, generator_text.lower(), found)

    # 4. html body signatures
    _scan_re(HTML_HINTS, body, found)

    host = urllib.parse.urlparse(r.url).netloc

    parts = []
    if server:
        parts.append("server: " + server)
    if powered:
        parts.append("powered-by: " + powered)
    if found:
        parts.append("detected: " + ", ".join(found[:8]))

    if not parts:
        return "No obvious stack signals from {} (plain, static or obfuscated).".format(host)

    return "{} \u2192 {}".format(host, " | ".join(parts))
