"""British Museum Collections via HTML pages.

The BM's `_search` JSON API returns a fixed fallback response for our TLS
fingerprint (probably persistent Cloudflare block since 2026-07-24). The
public HTML pages still work fine, so this module scrapes those:

  Search page: https://www.britishmuseum.org/collection/search?keyword=<q>
    (returns HTML with ~100 object links per page)

  Detail page: https://www.britishmuseum.org/collection/object/<unique_id>
    (returns HTML with og:title, og:description, og:image metadata)

curl_cffi impersonation still required to pass Cloudflare's TLS check.
No API key needed.
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
    from curl_cffi import requests as _cc
    return _cc.Session(impersonate="chrome124", timeout=45, verify=False)


def _clean_title(t: str) -> str:
    """og:title looks like `adire | British Museum`."""
    return t.split(" | ")[0].strip()


def search_ids(client, query: str, page: int = 0) -> list[str]:
    """Return unique object IDs from one search page (~100 per page)."""
    r = client.get(SEARCH_URL, params={"keyword": query, "page": page})
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

    # Search-result cache: {query: [ids...]}
    cache_key = f"bm-html__{country.replace(' ','_')}__{ethnicity.replace(' ','_')}"
    cache = raw_path("british-museum", cache_key)
    if cache.exists():
        try:
            search_data = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            search_data = {"ids": [], "details": {}}
    else:
        search_data = {"ids": [], "details": {}}

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
        if not any(tok in hay for tok in tokens):
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
        saved += 1

    # Save final cache
    cache.write_text(json.dumps(search_data, ensure_ascii=False), encoding="utf-8")

    if rejected_attribution:
        print(f"  bm attribution filter rejected {rejected_attribution}", flush=True)
    return saved
