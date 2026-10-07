"""Néprajzi Múzeum (Museum of Ethnography), Budapest: the Solr index behind its
online collection, https://gyujtemeny.neprajz.hu. Checked 2026-10-07.

    GET /solr/published/select?q=*:*&fq=img_s:*&rows=0&wt=json
            &facet=true&facet.field=search_ethnicity_hu_ss&facet.limit=-1
        the museum's people terms with counts: 484 values over 197,590
        records with an image ("sokác" 313, "palóc" 109, "bukovinai székely" 63)
    GET /solr/published/select?q=*:*&fq=search_ethnicity_hu_ss:("a" OR "b")
            &fq=img_s:*&fl=...&rows=100&start=N&wt=json
        the records; img_s is a path under the site ("multimedia/8/....large.jpg")

The people term is the museum's own field (search_ethnicity_hu_ss), in
Hungarian and singular: Wikidata's Hungarian names are plural ("palócok"), so
the plural ending is also taken off (singulars()). The art form comes from a
Hungarian keyword in the title (Hungarian puts the head noun last, "női ing"),
else from the museum's collection ("Kerámiagyűjtemény"). Sound recordings and
films are skipped: their picture is a label or a box. No open licence is stated:
the images are the museum's, shown linked to its record page.
"""
from __future__ import annotations

import functools
import re
import time
import unicodedata

import httpx

BASE = "https://gyujtemeny.neprajz.hu"
SOLR = f"{BASE}/solr/published/select"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (folk-patterns atlas research)"}
SKIP_COLLECTIONS = {"Dallam- és hangzóanyag-gyűjtemény", "Filmgyűjtemény"}
PHOTO_COLLECTIONS = {"Fényképgyűjtemény", "Diapozitív-gyűjtemény"}
_FL = "oid,list_title_hu_s,search_ethnicity_hu_ss,search_collection_hu_s,img_s"

COLLECTION_CLASS = {
    "Kerámiagyűjtemény": "ceramic", "Hangszergyűjtemény": "instruments",
    "Fényképgyűjtemény": "photo", "Diapozitív-gyűjtemény": "photo",
    "Textil- és viseletgyűjtemény": "textile", "Bútor- és világítóeszköz-gyűjtemény": "household",
    "Egyházi gyűjtemény": "masks-ritual", "Szokás- és játékgyűjtemény": "masks-ritual",
    "Rajz- és festménygyűjtemény": "painting-mss", "Nyomatgyűjtemény": "painting-mss",
    "Kéziratgyűjtemény": "painting-mss", "Építkezésgyűjtemény": "architectural",
    "Állattartás- és pásztorművészet-gyűjtemény": "household", "Földművelés-gyűjtemény": "household",
    "Halászatgyűjtemény": "household", "Gyűjtögetésgyűjtemény": "household",
    "Mesterséggyűjtemény": "household", "Közlekedésgyűjtemény": "household",
    "Táplálkozásgyűjtemény": "household",
}

# Hungarian object nouns, folded; the match that ends last wins ("kenderorsó" is
# a spindle, "női ing" a shirt).
KEYWORDS = [
    (r"\bing\b|ingvall|szoknya|kotény|melleny|kendo|suba|szur\b|csizma|cipo|bocskor|kalap|sapka|fokoto|parta\b|ujjas|"
     r"dolmany|nadrag|gatya|viselet|ruha|kabat|\bov\b|harisnya|kesztyu|fejdisz|papucs|bekecs|guba\b", "garment"),
    (r"himzes|szottes|abrosz|terito|parnahej|parnavég|lepedo|takaro|szonyeg|torulkozo|vaszon|csipke|textil|"
     r"guzsaly|orso\b|rokka|szovoszek|motolla", "textile"),
    (r"gyongy|nyaklanc|karperec|gyuru|fulbevalo|bross|kosonyu|ekszer|nyakek|\bcsat\b|halsz?alag", "jewelry"),
    (r"korso|\btal\b|tanyer|bokaly|csupor|kocsog|fazek|kancso|butella|cserep|keramia|edeny|szilke|kanta\b", "ceramic"),
    (r"duda|furulya|citera|hegedu|\bdob\b|\bsip\b|tilinko|hangszer|tambura|cimbalom|kerepl|koboz|okarina", "instruments"),
    (r"\bkes\b|kard\b|fokos|balta|\bij\b|\bnyil\b|landzsa|pajzs|puska|\btor\b", "arms"),
    (r"szobor|figura|babu\b|\bbaba\b|faragott alak", "sculpture"),
    (r"maszk|alarc|amulett|feszulet|\bkereszt\b", "masks-ritual"),
    (r"kanal|lada\b|\bszek\b|asztal|sotarto|kosar|szakajto|vodor|kulacs|borotvatok|tukros|sotarto|pipa\b|dohanyzacsko|"
     r"ivocsanak|merítő|bot\b|ostor|tarsoly|tarisznya", "household"),
    (r"fenykep|fotó|foto\b|diapozitiv", "photo"),
]


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if not unicodedata.combining(c)).lower()


_KW = [(re.compile(_fold(p)), a) for p, a in KEYWORDS]


def client() -> httpx.Client:
    return httpx.Client(timeout=60, headers=_UA, follow_redirects=True)


def _select(cl: httpx.Client, params: dict) -> dict:
    for wait in (5, 30, 120, 0):
        r = cl.get(SOLR, params={"wt": "json", **params})
        if r.status_code < 500 or not wait:
            r.raise_for_status()
            return r.json()
        time.sleep(wait)
    return {}


@functools.lru_cache(maxsize=1)
def groups() -> tuple[str, ...]:
    """Every people term that has a record with an image."""
    with client() as cl:
        j = _select(cl, {"q": "*:*", "fq": "img_s:*", "rows": 0, "facet": "true",
                         "facet.field": "search_ethnicity_hu_ss", "facet.limit": -1, "facet.mincount": 1})
    f = j["facet_counts"]["facet_fields"]["search_ethnicity_hu_ss"]
    return tuple(f[::2])


def singulars(name: str) -> list[str]:
    """A Hungarian plural people name and its singulars: "palócok" -> "palóc",
    "hantik" -> "hanti", "bukovinai székelyek" -> "bukovinai székely"."""
    n = name.strip()
    out = [n]
    if n.endswith("k") and len(n) > 4:
        out.append(n[:-1])
        if n[-2] in "oeöaá" and len(n) > 5:
            out.append(n[:-2])
    return out


def records(cl: httpx.Client, terms: list[str], cap: int, photos: bool) -> list[dict]:
    """Records with an image under any of the museum's people `terms`, photographs
    or objects, sound recordings and films left out."""
    def q(v: str) -> str:
        return '"' + v.replace('"', '\\"') + '"'
    coll = " OR ".join(q(c) for c in (PHOTO_COLLECTIONS if photos else PHOTO_COLLECTIONS | SKIP_COLLECTIONS))
    fq = ["img_s:*", "search_ethnicity_hu_ss:(" + " OR ".join(q(t) for t in terms) + ")",
          f"search_collection_hu_s:({coll})" if photos else f"-search_collection_hu_s:({coll})"]
    out: list[dict] = []
    start = 0
    while len(out) < cap:
        j = _select(cl, {"q": "*:*", "fq": fq, "fl": _FL, "rows": 100, "start": start, "sort": "oid asc"})
        docs = (j.get("response") or {}).get("docs") or []
        out += docs
        if len(docs) < 100:
            break
        start += 100
        time.sleep(0.3)
    return out[:cap]


def art_form(doc: dict) -> str:
    t = _fold(doc.get("list_title_hu_s") or "")
    best = None
    for rx, a in _KW:
        for m in rx.finditer(t):
            if best is None or m.end() > best[0]:
                best = (m.end(), a)
    if best:
        return best[1]
    return COLLECTION_CLASS.get(doc.get("search_collection_hu_s") or "", "unclassified")


def image_url(doc: dict) -> str:
    return f"{BASE}/{doc['img_s'].lstrip('/')}" if doc.get("img_s") else ""


def object_url(oid: str) -> str:
    return f"{BASE}/hu/collection/item/{oid}"
