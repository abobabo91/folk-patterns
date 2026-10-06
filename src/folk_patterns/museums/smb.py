"""Staatliche Museen zu Berlin (SMB) online collection, https://search.smb.museum.

The search site's own JSON API, read off its network traffic on 2026-10-06:

    GET /api/objects/search-expert?q=&start=&limit=&has_images=true
        -> {"artworks": [{"id": "obj-…", "titel", "objekttyp", "sammlung", "image": {"url"}}], "total"}
    GET /api/objects/detail/obj-<id>
        -> the record; `geography` lists places and peoples, a people with
           role "Ethnie" ({"searchLabel": "Mapuche", "role": "Ethnie"}, and
           "Araukaner" as its historical name)

The free-text search also hits titles and other museums (a Dürer for
"Laurentius"), so a hit counts only when its detail names the people under
role "Ethnie": the museum's own attribution, read as text.

Images are CC BY-NC-SA 4.0. Some records show a placeholder instead
("aus ethischen Gründen nicht gezeigt", withheld for ethical reasons);
those are skipped.
"""
from __future__ import annotations

import time

import httpx

BASE = "https://search.smb.museum"
_UA = {"User-Agent": "Mozilla/5.0 (folk-patterns atlas research)"}
# Collections with ethnographic holdings; a hit elsewhere is a name collision.
ETHNO_COLLECTIONS = {"Ethnologisches Museum", "Museum Europäischer Kulturen", "Museum für Asiatische Kunst",
                     "Museum für Islamische Kunst"}


def client() -> httpx.Client:
    return httpx.Client(timeout=60, headers=_UA)


def _get(cl: httpx.Client, url: str, **params) -> dict:
    """GET with backoff: the API answered 502 for a few minutes on 2026-10-06."""
    for wait in (5, 30, 120, 0):
        r = cl.get(url, params=params or None)
        if r.status_code < 500 or not wait:
            r.raise_for_status()
            return r.json()
        time.sleep(wait)
    return {}


def search(cl: httpx.Client, q: str, start: int = 0, limit: int = 100) -> dict:
    return _get(cl, f"{BASE}/api/objects/search-expert", q=q, start=start, limit=limit, has_images="true")


def detail(cl: httpx.Client, oid: str) -> dict:
    return _get(cl, f"{BASE}/api/objects/detail/{oid}")


def ethnie(d: dict) -> list[str]:
    """The peoples the record names as its attribution."""
    return [g.get("searchLabel") or "" for g in d.get("geography") or [] if g.get("role") == "Ethnie"]


def image_url(d: dict) -> str:
    """The large thumbnail of the first real image, '' when only a placeholder."""
    for im in d.get("images") or []:
        if "Platzhalter" in str(im.get("asset_content") or "") or not im.get("l_path"):
            continue
        return f"{BASE}/media{im['l_path']}"
    return ""


def object_url(oid: str) -> str:
    return f"https://id.smb.museum/object/{oid.removeprefix('obj-')}"
