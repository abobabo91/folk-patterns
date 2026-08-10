"""Victoria & Albert Museum API — https://developers.vam.ac.uk/

Two-endpoint flow:
  search:   GET /v2/objects/search?q=<term>&images=true      -> lightweight snippet
  deep:     GET /v2/museumobject/<systemNumber>              -> 60+ fields incl.
             materials, techniques, dimensions, historicalContext, provenance,
             additional images (multiple views), galleryLocations.

Each result is routed to a country by _primaryPlace via folk_patterns.places,
then merged with its deep-fetch response into the canonical schema, then
downloaded + saved.

No key required.
"""
from __future__ import annotations

import json
from typing import Any

from ..util import RateLimitedClient, download_image, append_metadata, library_path, raw_path
from ..places import route_place_to_country
from ..schema import from_va
from ..classify import classify

BASE = "https://api.vam.ac.uk/v2"


def search(client: RateLimitedClient, query: str, page_size: int = 100, max_pages: int = 5) -> list[dict]:
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        j = client.get_json(
            f"{BASE}/objects/search",
            params={"q": query, "images": "true", "page_size": page_size, "page": page},
        )
        records = j.get("records") or []
        out.extend(records)
        info = j.get("info") or {}
        if page >= (info.get("pages") or 1):
            break
    return out


def fetch_deep(client: RateLimitedClient, system_number: str) -> dict | None:
    """Fetch the deep /museumobject record. Cached per systemNumber."""
    cache = raw_path("va-deep", system_number)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    try:
        j = client.get_json(f"{BASE}/museumobject/{system_number}")
        rec = j.get("record") or j
        cache.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return rec
    except Exception as e:
        print(f"  ! v&a deep-fetch {system_number} failed: {e}", flush=True)
        return None


def scrape_tradition_routed(
    client: RateLimitedClient,
    region: str,
    tradition_name: str,
    tradition_ethnicity_by_country: dict[str, str],
    max_per_country: int = 50,
) -> dict[str, int]:
    """Query V&A once for a tradition, route each hit by _primaryPlace, then
    do the deep-fetch, canonicalize, download."""
    key = f"va__{tradition_name}"
    cache = raw_path("va", key)
    if cache.exists():
        results = json.loads(cache.read_text(encoding="utf-8"))
    else:
        results = search(client, tradition_name)
        cache.write_text(json.dumps(results), encoding="utf-8")

    if not results:
        return {}

    counts: dict[str, int] = {}
    for snippet in results:
        img_id = snippet.get("_primaryImageId")
        if not img_id:
            continue
        place = snippet.get("_primaryPlace") or ""
        obj_type = snippet.get("objectType") or ""
        country = route_place_to_country(region, place, obj_type)
        if country is None:
            continue
        if counts.get(country, 0) >= max_per_country:
            continue

        # If the tradition owner didn't specify an ethnicity for this
        # country, DON'T fall back to country-as-ethnicity — that produces
        # nonsense paths like china-xinjiang/china-xinjiang/ that build_index
        # then has to reroute. Use "_regional" as an explicit staging marker;
        # build_index's _route_regional will place the record into a real
        # ethnicity bucket (by tradition-owner match or country-majority) or
        # drop it cleanly.
        ethnicity = tradition_ethnicity_by_country.get(country, "_regional")
        sys_num = snippet.get("systemNumber")

        # Deep-fetch enriches with materials/techniques/history/dimensions/etc.
        deep = fetch_deep(client, sys_num)

        # Provisional classification from snippet
        cf = classify({
            "classification": obj_type,
            "object_type": obj_type,
            "material_technique": snippet.get("materialsAndTechniques") or "",
            "title": snippet.get("_primaryTitle") or "",
        })

        cultural = {
            "region": region,
            "country": country,
            "ethnicity": ethnicity,
            "tradition": tradition_name,
            "art_form": cf["art_form"],
            "pattern_density": cf["pattern_density"],
        }
        record = from_va(snippet, deep, cultural)

        # Download primary + alt images
        dest = library_path(region, country, ethnicity, cf["art_form"], tradition_name)
        for i, img in enumerate(record["images"]):
            role = img["role"]
            filename = f"va_{sys_num}" + (f"_{i}" if role != "primary" else "") + ".jpg"
            dst = dest / "images" / filename
            try:
                sha, size = download_image(client, img["url"], dst)
                img["sha256"] = sha
                img["bytes"] = size
                img["local_path"] = str(dst.relative_to(dest.parents[5]))
            except Exception as e:
                print(f"  ! v&a {sys_num} image {i} download failed: {e}", flush=True)

        # Filter out image entries whose download failed
        record["images"] = [i for i in record["images"] if i.get("local_path")]
        if not record["images"]:
            continue

        append_metadata(dest, record)
        counts[country] = counts.get(country, 0) + 1

    return counts
