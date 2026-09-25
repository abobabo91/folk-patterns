"""British Museum Collections via HTML pages.

The BM's `_search` JSON API returns a fixed fallback response for our TLS
fingerprint (probably persistent Cloudflare block since 2026-07-24). The
public HTML pages still work fine, so this module scrapes those:

  Search page: https://www.britishmuseum.org/collection/search?keyword=<q>
    (returns HTML with ~100 object links per page)

  Detail page: https://www.britishmuseum.org/collection/object/<unique_id>
    (returns HTML with og:title, og:description, og:image metadata)

Search by BM's own "Ethnic group" facet (`ethnic_name=San`) whenever it has
records for the culture. A bare keyword search runs over the whole collection,
print room included: `keyword=san` answers 15,290 results against 383 for
`ethnic_name=San`, `chin` 7,043 against 539, `thai` 1,531 against 53
(measured 2026-09-24), and on the full re-vet 34% of BM records were dropped,
most of them from those collisions. Keyword search is the fallback for a
culture the facet does not know (`Cham` has 1 facet record).

Cloudflare: since 2026-09-24 every curl_cffi impersonation gets 403. A
`cf_clearance` cookie earned by a real Chrome passes, used with that Chrome's
User-Agent. Set BM_CDP_URL (e.g. http://127.0.0.1:9226) to a Chrome with
remote debugging and `_client()` opens the search page there once and copies
the cookie. No API key needed.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..util import LIBRARY_DIR, download_image, append_metadata, library_path, raw_path

SEARCH_URL = "https://www.britishmuseum.org/collection/search"
DETAIL_URL = "https://www.britishmuseum.org/collection/object/{uid}"

_OBJECT_LINK_RE = re.compile(r'/collection/object/([A-Za-z0-9,._\-]+)')
_OG_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]+)"')
_OG_DESC_RE = re.compile(r'<meta property="og:description" content="([^"]+)"')

# Per-ethnicity reject patterns for the BM description text. These catch
# false-positives that pass the culture-string filter but describe something
# else entirely — e.g. "Cham" (Vietnamese ethnicity) collides with 19th-c.
# French cartoonist "Cham" (Amédée de Noé), whose humor books are catalogued
# under object type "print; book of prints; comic book".
_BM_ETHNONYM_REJECTS = {
    "cham": re.compile(
        r"\bpar CHAM\b|\bAm[eé]d[eé]e.*No[eé]|de No[eé], Am[eé]d"
        r"|\bcomic book\b|\bcaricaturist\b",
        re.I,
    ),
}
_OG_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]+)"')


def _client():
    import os
    from curl_cffi import requests as _cc
    s = _cc.Session(impersonate="chrome131", timeout=45, verify=False)
    cdp = os.environ.get("BM_CDP_URL")
    if cdp:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = p.chromium.connect_over_cdp(cdp).contexts[0]
            pg = ctx.new_page()
            pg.goto(SEARCH_URL + "?keyword=textile", wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(5000)
            ua = pg.evaluate("navigator.userAgent")
            pg.close()
            for c in ctx.cookies():
                if "britishmuseum" in c["domain"]:
                    s.cookies.set(c["name"], c["value"], domain=c["domain"])
        s.headers["User-Agent"] = ua
    r = s.get(SEARCH_URL, params={"keyword": "textile"})
    if r.status_code == 403:
        print("  ! British Museum answers 403 (Cloudflare) — set BM_CDP_URL", flush=True)
    return s


def _clean_title(t: str) -> str:
    """og:title looks like `adire | British Museum`."""
    return t.split(" | ")[0].strip()


def search_ids(client, query: str | None, page: int = 0,
               ethnic_name: str | None = None) -> list[str]:
    """Return unique object IDs from one search page (~100 per page)."""
    # image=true is the site's "Image only" toggle: objects without a photo
    # are useless to us and cost a detail fetch each (San: 304 of 383).
    params = {"page": page, "image": "true"}
    if query:
        params["keyword"] = query
    if ethnic_name:
        params["ethnic_name"] = ethnic_name
    r = client.get(SEARCH_URL, params=params)
    if r.status_code != 200:
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for m in _OBJECT_LINK_RE.finditer(r.text):
        oid = m.group(1)
        if oid not in seen:
            seen.add(oid)
            ids.append(oid)
    return ids


def fetch_detail(client, unique_id: str) -> dict | None:
    """Return {title, description, image_url} or None if the page is 404/empty."""
    r = client.get(DETAIL_URL.format(uid=unique_id))
    if r.status_code != 200:
        return None
    title_m = _OG_TITLE_RE.search(r.text)
    desc_m = _OG_DESC_RE.search(r.text)
    img_m = _OG_IMAGE_RE.search(r.text)
    if not (title_m and img_m):
        return None
    return {
        "title": _clean_title(title_m.group(1)),
        "description": desc_m.group(1) if desc_m else "",
        "image_url": img_m.group(1).replace("http://", "https://"),
    }


_LIBRARY_IDS: set[str] | None = None


def _in_library(unique_id: str) -> bool:
    """True when this BM object is already a record anywhere in the library.
    append_metadata REPLACES a same-id record, so re-saving one would wipe its
    vetting verdict and re-attribution and spend the per-culture cap on an
    object we already have."""
    global _LIBRARY_IDS
    if _LIBRARY_IDS is None:
        _LIBRARY_IDS = set()
        for mp in LIBRARY_DIR.glob("*/*/*/*/*/metadata.json"):
            try:
                for r in json.loads(mp.read_text(encoding="utf-8")):
                    if isinstance(r.get("id"), str) and r["id"].startswith("british_museum-"):
                        _LIBRARY_IDS.add(r["id"])
            except Exception:
                continue
    return f"british_museum-{unique_id}" in _LIBRARY_IDS


def _to_canonical(unique_id: str, detail: dict, cultural: dict) -> dict | None:
    from ..schema import _empty_record

    r = _empty_record("british_museum", unique_id)
    r["cultural"].update(cultural)
    r["physical"]["title"] = detail["title"]
    r["physical"]["summary"] = detail["description"]
    r["physical"]["classification"] = detail["title"]

    r["source"]["museum_name"] = "British Museum"
    r["source"]["accession_number"] = unique_id.replace("_", ",", 1).replace("-", ".")
    r["source"]["object_url"] = DETAIL_URL.format(uid=unique_id)
    r["source"]["credit_line"] = "© The Trustees of the British Museum"
    r["source"]["rights"] = "CC BY-NC-SA 4.0"

    r["location"]["current_museum"] = "British Museum"
    r["images"].append({
        "url": detail["image_url"], "iiif_id": None, "iiif_base": None,
        "role": "primary", "sha256": None, "bytes": None, "local_path": None,
    })
    r["raw"] = detail
    return r


def scrape_ethnicity(
    client, region: str, country: str, ethnicity: str,
    queries: list[str],
    max_per_query: int = 100,
    max_total: int = 60,
    accept_tokens: list[str] | None = None,
    tradition_tokens: list[str] | None = None,
    ethnic_name: str | None = None,
) -> int:
    """Search each query via HTML search page, then fetch each detail
    page. Attribution filter: keep a record if EITHER an ethnonym token
    OR a tradition-specific keyword appears in the title/description
    (case-insensitive).

    Ethnonym-only filter was too strict — BM records for `aso oke`,
    `adire`, `Gelede mask` legitimately identify Yoruba material but
    don't repeat the word "Yoruba" in the free-text description.
    Adding tradition tokens (from the seed's `traditions` list) rescues
    them without letting truly unrelated records through.
    """
    from ..classify import classify
    from ..junk import should_reject

    tokens = set()
    for t in (accept_tokens or []):
        if t:
            tokens.add(t.strip().lower())
    tokens.add(ethnicity.lower())
    tokens.add(ethnicity.split()[0].lower())
    tokens.add(ethnicity.split(" (")[0].lower())
    # Add tradition-specific keywords (aso oke, adire, tongkonan, ...).
    # These are unambiguous ethnonym-equivalents in museum catalogs.
    for t in (tradition_tokens or []):
        if t:
            tokens.add(t.strip().lower())
    tokens = {t for t in tokens if len(t) >= 3}

    # BM's own ethnic attribution first; keyword search only when it has none.
    facet = (ethnic_name or ethnicity.split(" (")[0]).strip()
    facet_ids: list[str] = []
    for page in range(0, 40):   # BM pages are 0-based: page=0 is the first
        try:
            ids = search_ids(client, None, page=page, ethnic_name=facet)
        except Exception as e:
            print(f"  ! bm facet {facet!r} page {page} failed: {e}", flush=True)
            break
        new = [i for i in ids if i not in facet_ids]
        if not new:
            break
        facet_ids += new
        time.sleep(0.3)
    use_facet = len(facet_ids) >= 20
    print(f"  bm ethnic_name={facet!r}: {len(facet_ids)} ids -> "
          f"{'facet' if use_facet else 'keyword search'}", flush=True)

    # Search-result cache: {query: [ids...]}
    cache_key = (f"bm-{'facet' if use_facet else 'html'}__"
                 f"{country.replace(' ','_')}__{ethnicity.replace(' ','_')}")
    cache = raw_path("british-museum", cache_key)
    if cache.exists():
        try:
            search_data = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            search_data = {"ids": [], "details": {}}
    else:
        search_data = {"ids": [], "details": {}}

    if use_facet:
        # The facet listing is cheap and changes; detail pages are cached.
        # BM lists by object name, so a capped run would take only the A-B
        # object types ("adze", "amulet"); a fixed-seed shuffle spreads it.
        import random
        search_data["ids"] = sorted(facet_ids)
        random.Random(0).shuffle(search_data["ids"])
        search_data.setdefault("details", {})
    if not search_data.get("ids"):
        all_ids: list[str] = []
        seen: set[str] = set()
        for q in queries:
            try:
                ids = search_ids(client, q, page=0)
            except Exception as e:
                print(f"  ! bm search {q!r} failed: {e}", flush=True)
                continue
            for oid in ids:
                if oid not in seen:
                    seen.add(oid)
                    all_ids.append(oid)
            time.sleep(0.3)
        search_data["ids"] = all_ids
        search_data["details"] = {}
        cache.write_text(json.dumps(search_data, ensure_ascii=False), encoding="utf-8")

    if not search_data["ids"]:
        return 0

    saved = 0
    rejected_attribution = 0
    fetched = 0
    for uid in search_data["ids"]:
        if saved >= max_total:
            break
        if _in_library(uid):
            continue

        # Detail cache to avoid re-fetching
        detail = search_data["details"].get(uid)
        if detail is None:
            try:
                detail = fetch_detail(client, uid)
                fetched += 1
                time.sleep(0.4)  # polite pacing
            except Exception as e:
                print(f"  ! bm detail {uid} failed: {e}", flush=True)
                detail = False   # sentinel: don't re-fetch
            search_data["details"][uid] = detail
            # Save cache incrementally in case we crash mid-scrape
            if fetched % 20 == 0:
                cache.write_text(json.dumps(search_data, ensure_ascii=False), encoding="utf-8")

        if not detail:
            continue

        # Attribution + junk filters
        hay = f"{detail['title']} {detail['description']}".lower()
        if not use_facet and not any(tok in hay for tok in tokens):
            rejected_attribution += 1
            continue
        if should_reject(detail["title"], detail["description"], "British Museum")[0]:
            continue
        # Ambiguous-ethnonym pen-name / author collisions. Cham matches French
        # cartoonist Amédée de Noé (pen-name "CHAM"). Add more here as they
        # surface — the reject-pattern list is per-ethnicity substring.
        _pat = _BM_ETHNONYM_REJECTS.get(ethnicity.lower())
        if _pat and _pat.search(f"{detail['title']} {detail['description']}"):
            rejected_attribution += 1
            continue

        cf = classify({
            "classification": detail["title"], "object_type": detail["title"], "title": detail["title"],
            "medium": "", "material_technique": "",
            "summary": detail["description"], "description": detail["description"],
        })
        cultural = {
            "region": region, "country": country, "ethnicity": ethnicity,
            "tradition": queries[0] if queries else ethnicity,
            "art_form": cf["art_form"], "pattern_density": cf["pattern_density"],
        }
        rec = _to_canonical(uid, detail, cultural)
        if not rec:
            continue

        dest = library_path(region, country, ethnicity, cf["art_form"], queries[0] if queries else ethnicity)
        fname = f"bm_{uid.replace(',','_').replace('.','_')}.jpg"
        dst = dest / "images" / fname
        try:
            sha, size = download_image(client, rec["images"][0]["url"], dst)
            rec["images"][0]["sha256"] = sha
            rec["images"][0]["bytes"] = size
            rec["images"][0]["local_path"] = str(dst.relative_to(dest.parents[5]))
        except Exception as e:
            print(f"  ! bm {uid} img download failed: {e}", flush=True)
            continue
        append_metadata(dest, rec)
        if _LIBRARY_IDS is not None:
            _LIBRARY_IDS.add(rec["id"])
        saved += 1

    # Save final cache
    cache.write_text(json.dumps(search_data, ensure_ascii=False), encoding="utf-8")

    if rejected_attribution:
        print(f"  bm attribution filter rejected {rejected_attribution}", flush=True)
    return saved
