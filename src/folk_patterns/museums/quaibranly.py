"""Musée du quai Branly – Jacques Chirac, Paris: the collections site's query proxy.

No documented API; the site posts its queries to one endpoint, read off its
network traffic on 2026-10-06:

    POST https://collections.quaibranly.fr/ccProxy.ashx
         {"action": "get", "command": "search", "query": "/Record/IThesTerm/Term=Shipibo",
          "range": "1-200", "fields": "...", "responseformat": "json"}

The people is a term of the museum's ethnonym thesaurus ("Thésaurus des
ethnonymes", codes MQY…, in French: "Kurdes"), held in two places:

- objects: `Ethnonyme` (and the same name in `Obj_etnoplat.ethnie`), queried
  as /Record/Ethnonyme/Term=<name>. Counted 2026-10-06: Wayana 1,087,
  Bororo 522, Yanomami 459, Huichol 454, Quechua 417, Mapuche 317, Aymara 300.
- photographs: `IThesTerm` entries of Type "Populations", queried as
  /Record/IThesTerm/Term=<name> (Hmong 642, Mapuche 449). Objects have no
  Populations IThesTerm, so this query finds photographs only.

A record counts only when one of those terms is the people's name.

Images come through the site's own resizing proxy. The records expose no
stable public URL, so an object links to the site's search for its
inventory number.
"""
from __future__ import annotations

import time
from urllib.parse import quote

import httpx

PROXY = "https://collections.quaibranly.fr/ccProxy.ashx"
_UA = {"User-Agent": "Mozilla/5.0 (folk-patterns atlas research)"}
FIELDS = "*"   # a field list drops the nested IThesTerm and IImages


def client() -> httpx.Client:
    return httpx.Client(timeout=90, headers=_UA)


def _search(cl: httpx.Client, query: str, rng: str) -> dict:
    for wait in (3, 15, 60, 0):
        r = cl.post(PROXY, json={"action": "get", "command": "search", "query": query, "range": rng,
                                 "fields": FIELDS, "responseformat": "json"})
        if r.status_code < 500:
            r.raise_for_status()
            return r.json()
        if not wait:
            r.raise_for_status()
        time.sleep(wait)
    return {}


def records(cl: httpx.Client, term: str, cap: int, photos: bool = False, page: int = 100) -> list[dict]:
    """Objects (or, with `photos`, photographs) tagged with ethnonym `term`, up to `cap`."""
    query = f"/Record/IThesTerm/Term={term}" if photos else f"/Record/Ethnonyme/Term={term}"
    out: list[dict] = []
    start = 1
    while len(out) < cap:
        j = _search(cl, query, f"{start}-{start + page - 1}")
        recs = (j.get("records") or {}).get("record") or []
        if isinstance(recs, dict):   # a single hit is not wrapped in a list
            recs = [recs]
        out += [x["data"]["Record"] for x in recs if (x.get("data") or {}).get("Record", {}).get("ccObjectID")]
        count = int((j.get("request") or {}).get("count") or 0)
        start += page
        if not recs or start > count:
            break
        time.sleep(0.3)
    return out[:cap]


def _list(v) -> list:
    return v if isinstance(v, list) else [v] if v else []


def populations(rec: dict) -> list[str]:
    """The people terms of a record: an object's Ethnonyme and etnoplat ethnie,
    a photograph's Populations terms."""
    out = [t.get("Term") or "" for t in _list(rec.get("IThesTerm")) if t.get("Type") == "Populations"]
    out += [t.get("Term") or "" for t in _list(rec.get("Ethnonyme")) if "archéolog" not in str(t.get("Type") or "")]
    out += [str(e.get("ethnie") or "") for e in _list(rec.get("Obj_etnoplat"))]
    return list(dict.fromkeys(x for x in out if x and x != "non renseignée"))


def title(rec: dict) -> str:
    t = rec.get("Title")
    t = next((x.get("Title") for x in _list(t) if isinstance(x, dict) and x.get("Title")), None) if not isinstance(t, str) else t
    return str(t or rec.get("SortTitle") or rec.get("ObjectName") or "").strip()


def image_url(rec: dict) -> str:
    for im in _list(rec.get("Image")) + _list(rec.get("IImages")):   # objects / photographs
        path = str(im.get("image2") or "").replace("\\", "/")
        if path:
            return ("https://collections.quaibranly.fr/cc/imageproxy.ashx?server=localhost&port=15012"
                    f"&filename={quote(path)}&width=400&height=400&cache=yes")
    return ""


def number(rec: dict) -> str:
    """Inventory number: ObjectNumber on objects, ObjectNumber2 on photographs."""
    return str(rec.get("ObjectNumber") or rec.get("ObjectNumber2") or rec.get("ccObjectID"))


def object_url(number: str) -> str:
    return f"https://collections.quaibranly.fr/#/search?q={quote(number)}"
