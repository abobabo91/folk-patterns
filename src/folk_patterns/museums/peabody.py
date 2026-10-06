"""Peabody Museum of Archaeology & Ethnology, Harvard: its eMuseum site,
https://collections.peabody.harvard.edu.

The site's JSON export answers 403, so the HTML pages are read (checked
2026-10-06):

    GET /search/<text>/objects
        the culture facet: links carrying filter=cultureThesFilter:<term URI>
        labelled "Tlingit (1,227)", "Haida (180)"
    GET /search/*/objects?filter=<that filter>;department:Ethnographic&page=N
        24 rows a page: object id, title, classification, image id
        (filters combine with ";"; a second filter= parameter is ignored;
        department "Photographic" holds the field photographs)

A people's objects are the rows under a culture term whose label is the
people's name (world_peoples._ethno_match), so the museum's own field decides.
Images come from the site's media dispatcher in its "preview" size.
"""
from __future__ import annotations

import html
import re
import time
from urllib.parse import quote

import httpx

BASE = "https://collections.peabody.harvard.edu"
_UA = {"User-Agent": "Mozilla/5.0 (folk-patterns atlas research)"}
_FACET = re.compile(r'<a[^>]+href="[^"]*[?&]filter=(cultureThesFilter[^"#&]*)[^"]*"[^>]*>(.*?)</a>', re.S)
_ROW = re.compile(r'<tr class="item">(.*?)</tr>', re.S)


def client() -> httpx.Client:
    return httpx.Client(timeout=60, headers=_UA, follow_redirects=True)


def _get(cl: httpx.Client, url: str) -> str:
    for wait in (5, 30, 120, 0):
        r = cl.get(url)
        if r.status_code < 500 or not wait:
            r.raise_for_status()
            return r.text
        time.sleep(wait)
    return ""


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def cultures(cl: httpx.Client, name: str) -> list[tuple[str, str]]:
    """[(label, filter)] of the culture facet for a text search, "Haida (180)" -> "Haida"."""
    t = _get(cl, f"{BASE}/search/{quote(name)}/objects")
    out = []
    for f, label in _FACET.findall(t):
        label = re.sub(r"\s*\([\d,]+\)\s*$", "", _text(label))
        if label and not label.startswith("Load all"):
            out.append((label, html.unescape(f)))
    return out


def records(cl: httpx.Client, flt: str, cap: int, department: str = "Ethnographic") -> list[dict]:
    """Rows with an image under one culture filter in one department:
    {id, title, classification, image}."""
    flt = f"{flt}%3Bdepartment%3A{quote(department)}"
    out: list[dict] = []
    page = 1
    while len(out) < cap:
        t = _get(cl, f"{BASE}/search/*/objects?filter={flt}&page={page}")
        rows = _ROW.findall(t)
        for row in rows:
            oid = re.search(r"/objects/details/(\d+)", row)
            img = re.search(r"/internal/media/dispatcher/(\d+)/", row)
            title = re.search(r'class="titleClass">(.*?)</td>', row, re.S)
            cls = re.search(r'class="classificationTermsClass">(.*?)</td>', row, re.S)
            if oid and img:
                out.append({"id": oid.group(1), "image": img.group(1),
                            "title": _text(title.group(1)) if title else "",
                            "classification": _text(cls.group(1)) if cls else ""})
        if len(rows) < 24:
            break
        page += 1
        time.sleep(0.5)
    return out[:cap]


def image_url(media_id: str) -> str:
    return f"{BASE}/internal/media/dispatcher/{media_id}/preview"


def object_url(oid: str) -> str:
    return f"{BASE}/objects/details/{oid}"
