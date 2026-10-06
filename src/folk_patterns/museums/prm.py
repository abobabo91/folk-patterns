"""Pitt Rivers Museum, Oxford: the collections-online API (GLAM Digital).

Read off https://www.prm.ox.ac.uk/collections-online on 2026-10-06; no key:

    GET /v2/search/prm/party_group?q=(<name>)&size=     cultural-group names ("Konyak Naga")
    GET /v2/search-fields/prm/catalogue?culturalGroups.culturalGroup="<group>"&from=&size=
        -> {"total", "results": [{"item": {"id", "collection", "recordSubtitle", "multimedia"}}]}
    GET /v2/item/<id>/full                               the whole record

Every catalogue record lists its `culturalGroups` ({"culturalGroup": "Sangtam
Naga", "certainty": "uncertain", "culturalGroupHierarchy": "Southern Asia ->
Naga -> Sangtam Naga"}). The fielded search matches the group exactly, so a
people's objects are found by text alone. Images are the published
multimedia's file on the museum's S3 bucket (37 of 38 sampled loaded).
"""
from __future__ import annotations

import time

import httpx

API = "https://prd-online.glamdigital.io/v2"
ASSETS = "https://prm-online-collections-assets-prd.s3.eu-west-1.amazonaws.com/assets/"
_UA = {"User-Agent": "Mozilla/5.0 (folk-patterns atlas research)"}


def client() -> httpx.Client:
    return httpx.Client(timeout=60, headers=_UA)


def groups(cl: httpx.Client, name: str) -> list[str]:
    r = cl.get(f"{API}/search/prm/party_group", params={"q": f"({name})", "from": 0, "size": 30})
    r.raise_for_status()
    return [x["item"]["recordTitle"] for x in r.json().get("results") or []]


def records(cl: httpx.Client, group: str, cap: int, page: int = 100) -> list[dict]:
    """Up to `cap` records of a cultural group that have a published image."""
    out: list[dict] = []
    start = 0
    while len(out) < cap:
        r = cl.get(f"{API}/search-fields/prm/catalogue",
                   params={"culturalGroups.culturalGroup": f'"{group}"', "from": start, "size": page})
        r.raise_for_status()
        j = r.json()
        items = [x["item"] for x in j.get("results") or []]
        out += [i for i in items if any(m.get("isPublished") == "Yes" and m.get("identifier") for m in i.get("multimedia") or [])]
        start += len(items)
        if not items or start >= j.get("total", 0):
            break
        time.sleep(0.2)
    return out[:cap]


def image_url(item: dict) -> str:
    m = next(m for m in item["multimedia"] if m.get("isPublished") == "Yes" and m.get("identifier"))
    return ASSETS + m["identifier"]


def object_url(oid: str) -> str:
    return f"https://www.prm.ox.ac.uk/collections-online#/item/{oid}"
