"""Shared Sketchfab API client used by both the IRC plugin and agent tools."""

import requests

API_BASE = "https://api.sketchfab.com/v3"
MAX_FILE_BYTES = 50 * 1024 * 1024


def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Token {api_key}"}


def fmt_license(lic) -> str:
    if not lic:
        return "Unknown"
    if isinstance(lic, str):
        return lic
    return lic.get("label") or lic.get("slug") or "Unknown"


def parse_model(raw: dict) -> dict:
    thumbs = (raw.get("thumbnails") or {}).get("images") or []
    return {
        "uid": raw["uid"],
        "name": raw.get("name", "Unknown"),
        "user": (raw.get("user") or {}).get("username", "?"),
        "faces": raw.get("faceCount"),
        "verts": raw.get("vertexCount"),
        "likes": raw.get("likeCount", 0),
        "license": fmt_license(raw.get("license")),
        "thumb": thumbs[0]["url"] if thumbs else None,
        "url": raw.get("viewerUrl")
        or f"https://sketchfab.com/3d-models/{raw['uid']}",
    }


def search(api_key: str, query: str, count: int = 10) -> list[dict]:
    params: dict = {
        "type": "models",
        "downloadable": "true",
        "sort_by": "-likeCount",
        "count": count,
        "q": query,
    }
    r = requests.get(
        f"{API_BASE}/search",
        params=params,
        headers=auth_headers(api_key),
        timeout=10,
    )
    r.raise_for_status()
    return [parse_model(m) for m in r.json().get("results", [])]


def download_model(api_key: str, uid: str) -> tuple[bytes, str]:
    r = requests.get(
        f"{API_BASE}/models/{uid}/download",
        headers=auth_headers(api_key),
        timeout=10,
    )
    r.raise_for_status()
    links = r.json()

    for fmt in ("glb", "gltf"):
        link = links.get(fmt)
        if not link:
            continue
        resp = requests.get(link["url"], timeout=120, stream=True)
        resp.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ValueError(
                    f"File exceeds {MAX_FILE_BYTES // (1024 * 1024)}MB limit"
                )
            chunks.append(chunk)
        return b"".join(chunks), fmt

    raise ValueError("No GLB/GLTF download available")
