"""KAMIS "Коллекция онлайн" sites: the Russian Museum of Ethnography (REM) and
the Kunstkamera (Peter the Great Museum of Anthropology and Ethnography).

Both run the same platform and the same undocumented JSON API, read off the
sites' own network traffic on 2026-10-06:

    POST /api/search-entities/OBJECT        {"query", "start", "count", "filters", ...}
         -> {"data": [{"id", "title", "image"}], "totalCount"}
    POST /api/search-clarify-filters/OBJECT same body
         -> [{"attribute": "ethnos" | "fund" | ..., "data": [{"value", "title", "count"}]}]
    GET  /api/entity/OBJECT/<id>            the record's fields

Every record carries the museum's own ethnic attribution, `ethnos`, an id in a
controlled list ("марийцы" 11043, "марийцы горные" 11051). A people is matched
to those ids by name, so no object is assigned by reading pictures. The facet
answer lists only the top ~11 values, so it is asked per people name, never
once for the whole vocabulary.

Kunstkamera's certificate chain lacks an intermediate that Python's store
does not have (curl on Windows accepts it), so verification is off for these
read-only public pages.

Terms: REM allows personal, non-commercial and educational use with a link to
the site; Kunstkamera reserves all rights. The atlas shows the museum's own
thumbnail URL and links to the record, as for every other source.
"""
from __future__ import annotations

import time

import httpx

SITES = {
    "rem": "https://collection.ethnomuseum.ru",
    "kunstkamera": "https://collection.kunstkamera.ru",
}
_UA = {"User-Agent": "Mozilla/5.0 (folk-patterns atlas research)", "Content-Type": "application/json"}


def client() -> httpx.Client:
    return httpx.Client(timeout=90, headers=_UA, verify=False)


def _body(query: str, filters: dict, start: int = 0, count: int = 1) -> dict:
    return {"query": query, "start": start, "count": count, "filters": filters,
            "sort": None, "rawDataFilters": {}, "useAndForFields": []}


def _post(cl: httpx.Client, url: str, body: dict, want: type):
    """POST with backoff. Under load the sites sometimes answer 5xx, or 200
    with an error object where a list belongs (it stopped a run on 2026-10-06)."""
    for wait in (3, 15, 60, 0):
        r = cl.post(url, json=body)
        if r.status_code < 500:
            r.raise_for_status()
            j = r.json()
            if isinstance(j, want):
                return j
        if not wait:
            raise ValueError(f"{url}: {r.status_code} {r.text[:120]}")
        time.sleep(wait)


def facets(cl: httpx.Client, site: str, query: str, filters: dict | None = None) -> dict[str, list[dict]]:
    """{attribute: [{value, title, count}]} for the records matching `query`."""
    j = _post(cl, f"{SITES[site]}/api/search-clarify-filters/OBJECT", _body(query, filters or {}), list)
    return {f["attribute"]: f["data"] for f in j}


def records(cl: httpx.Client, site: str, filters: dict, cap: int, page: int = 100) -> list[dict]:
    """Up to `cap` records with an image matching `filters`, in the site's order."""
    out: list[dict] = []
    start = 0
    while len(out) < cap:
        data = _post(cl, f"{SITES[site]}/api/search-entities/OBJECT", _body("", filters, start, page), dict).get("data") or []
        out += [d for d in data if d.get("image") and not d.get("deleted")]
        if len(data) < page:
            break
        start += page
        time.sleep(0.2)
    return out[:cap]


def image_url(site: str, path: str) -> str:
    return SITES[site] + path


def object_url(site: str, oid: str) -> str:
    return f"{SITES[site]}/entity/OBJECT/{oid}"


def object_name(title: str) -> str:
    """The object part of a KAMIS title: "Женский костюм. Марийцы: ..." -> "Женский костюм"."""
    return title.split(". ")[0].strip()[:120]
