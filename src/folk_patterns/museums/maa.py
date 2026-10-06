"""Museum of Archaeology and Anthropology, Cambridge (MAA) collections site.

No JSON API; the server-rendered pages, read on 2026-10-06:

    GET /objects/?advanced_search=[{"field":"culture_group","value":"Gond"}]&page=N
        10 results a page: thumbnail, record link ("499971"), short description, place
    GET /objects/<id>/
        the record, with "Cultural Affliation" (sic) and its images under
        /media/library_images/web/

The advanced search on culture_group is a text search, so each hit's record
is read and kept only when its Cultural Affiliation names the people. Hits
without a thumbnail are skipped before that read.
"""
from __future__ import annotations

import html
import json
import re
import time

import httpx

BASE = "https://collections.maa.cam.ac.uk"
_UA = {"User-Agent": "Mozilla/5.0 (folk-patterns atlas research)"}
_CARD = re.compile(r'<a href="(\d+)">\s*<img[^>]*src="(/media/library_images/thumbnail/[^"]+)"', re.S)


def client() -> httpx.Client:
    return httpx.Client(timeout=60, headers=_UA, follow_redirects=True)


def _get(cl: httpx.Client, url: str, **params) -> httpx.Response:
    """GET with backoff: the server answers 429/5xx under parallel load."""
    for wait in (2, 8, 30, 0):
        r = cl.get(url, params=params or None)
        if r.status_code not in (429, 500, 502, 503, 504) or not wait:
            return r
        time.sleep(wait)
    return r


def search(cl: httpx.Client, value: str, max_pages: int = 40) -> list[str]:
    """Record ids with an image whose culture group text-matches `value`."""
    q = json.dumps([{"field": "culture_group", "value": value}])
    ids: list[str] = []
    for page in range(1, max_pages + 1):
        r = _get(cl, f"{BASE}/objects/", advanced_search=q, page=page)
        if r.status_code == 404:
            break
        r.raise_for_status()
        found = _CARD.findall(r.text)
        ids += [i for i, _ in found]
        m = re.search(r"Page \d+ of (\d+)", r.text)
        if not m or page >= int(m.group(1)):
            break
        time.sleep(1.0)
    return list(dict.fromkeys(ids))


def _lines(page: str) -> list[str]:
    text = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    return [x.strip() for x in text.split("\n") if x.strip()]


def detail(cl: httpx.Client, oid: str) -> dict:
    r = _get(cl, f"{BASE}/objects/{oid}/")
    r.raise_for_status()
    lines = _lines(r.text)

    def field(name: str) -> str:
        try:
            return lines[lines.index(name) + 1]
        except (ValueError, IndexError):
            return ""
    img = re.search(r"/media/library_images/web/[^\"'\s]+", r.text)
    return {"title": field("Description"), "culture": field("Cultural Affliation") or field("Cultural Affiliation"),
            "place": field("Place"), "image_url": BASE + img.group(0) if img else ""}


def object_url(oid: str) -> str:
    return f"{BASE}/objects/{oid}/"
