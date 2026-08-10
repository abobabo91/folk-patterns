"""Smithsonian Open Access API — https://edan.si.edu/openaccess/apidocs/

Keyed via api.data.gov key (stored in tools/vault/vault.toml [apis.smithsonian]).
Covers ~19 Smithsonian museums including:
  - CHNDM   Cooper Hewitt Design Museum (huge textile / decorative arts holdings)
  - FSG     Freer / Sackler (Asian art)
  - NMAfA   National Museum of African Art
  - NMAI    National Museum of the American Indian
  - NMAAHC  National Museum of African American History and Culture
  - NMAH    National Museum of American History
  - NMNHANTHRO  Anthropology (National Museum of Natural History)

We always require `online_media_type:"Images"` so we don't ingest library
records or metadata-only entries.
"""
from __future__ import annotations

import json
import os

from ..util import RateLimitedClient, download_image, append_metadata, library_path, raw_path
from ..places import route_place_to_country
from ..schema import from_smithsonian
from ..classify import classify
from ..junk import should_reject
from ..rejects import log_reject

BASE = "https://api.si.edu/openaccess/api/v1.0"

# Unit codes we NEVER want, even if the query-time exclusion misses them
# (older cached rows, upstream schema change). NMNH natural-history: botany
# herbaria, bird / mammal / fish / entomology specimens — none of which are
# folk material culture. Belt-and-suspenders check applied at ingest.
_EXCLUDE_UNITS = {
    "NMNHBOTANY", "NMNHENTO", "NMNHFISHES", "NMNHHERPETOLOGY", "NMNHINV",
    "NMNHMAMMALS", "NMNHBIRDS", "NMNHPALEO", "NMNHMINSCI",
}


def _get_key() -> str:
    """Read api.data.gov key from vault.toml. Falls back to env var."""
    if "SMITHSONIAN_API_KEY" in os.environ:
        return os.environ["SMITHSONIAN_API_KEY"]
    from pathlib import Path
    vault_path = Path(__file__).resolve().parents[4] / "tools" / "vault" / "vault.toml"
    if vault_path.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore
        data = tomllib.loads(vault_path.read_text(encoding="utf-8"))
        key = ((data.get("apis") or {}).get("smithsonian") or {}).get("key")
        if key:
            return key
    raise RuntimeError("No Smithsonian API key. Set SMITHSONIAN_API_KEY or add to vault.")


def search(client: RateLimitedClient, query: str, max_rows: int = 200,
           page_size: int = 100) -> list[dict]:
    """Paginated search. Always filters to image-bearing records AND excludes
    NMNH natural-history departments at the query layer.

    Historical bug: querying "batak" or "toraja" returned 40+ Philippine/
    Indonesian herbarium specimens (Diospyros ulo Merr., Caulerpa taxifolia,
    Palisada perforata …) because NMNH Botany tags them with the collection
    locality. We downloaded them, then the build-time junk filter dropped
    them — pure bandwidth waste. Adding NOT unit_code:(...) at query time
    stops the leak at the source."""
    key = _get_key()
    # Exclude NMNH natural-history units. Unit codes from
    # https://naturalhistory.si.edu/research/collections
    _NH_UNITS = (
        "NMNHBOTANY",       # Botany (herbarium)
        "NMNHENTO",         # Entomology
        "NMNHFISHES",       # Fishes
        "NMNHHERPETOLOGY",  # Herpetology
        "NMNHINV",          # Invertebrate Zoology
        "NMNHMAMMALS",      # Vertebrate Zoology - Mammals
        "NMNHBIRDS",        # Vertebrate Zoology - Birds
        "NMNHPALEO",        # Paleobiology
        "NMNHMINSCI",       # Mineral Sciences
    )
    nh_exclusion = " NOT unit_code:(" + " OR ".join(_NH_UNITS) + ")"
    q = f"{query} AND online_media_type:\"Images\"" + nh_exclusion
    out: list[dict] = []
    start = 0
    while len(out) < max_rows:
        rows_this = min(page_size, max_rows - len(out))
        j = client.get_json(
            f"{BASE}/search",
            params={"q": q, "rows": rows_this, "start": start, "api_key": key},
        )
        rows = (j.get("response") or {}).get("rows") or []
        out.extend(rows)
        row_count = (j.get("response") or {}).get("rowCount") or 0
        if not rows or start + len(rows) >= row_count:
            break
        start += len(rows)
    return out


def scrape_tradition_routed(
    client: RateLimitedClient,
    region: str,
    tradition_name: str,
    tradition_ethnicity_by_country: dict[str, str],
    max_per_country: int = 40,
) -> dict[str, int]:
    """Same shape as V&A's routed scraper. Query by tradition, route each hit
    by `content.freetext.place` (or indexedStructured.place) via places.py."""
    key = f"si__{tradition_name}"
    cache = raw_path("si", key)
    if cache.exists():
        rows = json.loads(cache.read_text(encoding="utf-8"))
    else:
        rows = search(client, tradition_name, max_rows=200)
        cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    if not rows:
        return {}

    counts: dict[str, int] = {}
    for row in rows:
        content = row.get("content") or {}
        ft = content.get("freetext") or {}
        ix = content.get("indexedStructured") or {}
        dnr = content.get("descriptiveNonRepeating") or {}

        # Reject natural-history specimens regardless of query-time exclusion.
        # Cached rows from before the query filter existed and future upstream
        # schema changes are both covered by this ingest-time guard.
        if (dnr.get("unit_code") or "") in _EXCLUDE_UNITS:
            log_reject(source="smithsonian", reason="natural-history-unit",
                       region=region, tradition=tradition_name,
                       title=(dnr.get("title") or {}).get("content"),
                       extra={"unit": dnr.get("unit_code")})
            continue

        # Junk-title gate — Latin binomials, camera-dump filenames, colonial
        # admin text, etc. Same filter Cleveland already runs.
        title = (dnr.get("title") or {}).get("content") or ""
        notes_arr = ft.get("notes") or []
        summary = notes_arr[0].get("content") if notes_arr and isinstance(notes_arr[0], dict) else ""
        is_junk, junk_reason = should_reject(title, summary or "", "Smithsonian")
        if is_junk:
            log_reject(source="smithsonian", reason=f"junk-{junk_reason}",
                       region=region, tradition=tradition_name, title=title)
            continue

        # extract place
        place = None
        pl_arr = ft.get("place") or []
        if pl_arr and isinstance(pl_arr[0], dict):
            place = pl_arr[0].get("content")
        if not place:
            place = (ix.get("place") or [None])[0]
        # object_type for signature-tradition fallback in the router
        obj_type = None
        ot_arr = ft.get("objectType") or []
        if ot_arr and isinstance(ot_arr[0], dict):
            obj_type = ot_arr[0].get("content")

        country = route_place_to_country(region, place or "", obj_type or "")
        if country is None:
            log_reject(source="smithsonian", reason="place-not-in-region",
                       region=region, tradition=tradition_name, title=title,
                       extra={"place": place})
            continue
        if counts.get(country, 0) >= max_per_country:
            continue

        # Same _regional fallback as V&A — don't dump generic country name
        # into the ethnicity slot (produces china-xinjiang/china-xinjiang/…).
        ethnicity = tradition_ethnicity_by_country.get(country, "_regional")

        # classify
        classify_input = {
            "classification": obj_type or "",
            "object_type": obj_type or "",
            "title": (content.get("descriptiveNonRepeating") or {}).get("title", {}).get("content") or "",
        }
        cf = classify(classify_input)

        cultural = {
            "region": region,
            "country": country,
            "ethnicity": ethnicity,
            "tradition": tradition_name,
            "art_form": cf["art_form"],
            "pattern_density": cf["pattern_density"],
        }
        record = from_smithsonian(row, cultural)

        # Download images
        dest = library_path(region, country, ethnicity, cf["art_form"], tradition_name)
        # Derive a filename-safe id from the SI record_ID
        rec_id = ((content.get("descriptiveNonRepeating") or {}).get("record_ID")
                  or row.get("id") or "unknown").replace(":", "_").replace("/", "_")
        for i, img in enumerate(record["images"]):
            role = img["role"]
            filename = f"si_{rec_id}" + (f"_{i}" if role != "primary" else "") + ".jpg"
            dst = dest / "images" / filename
            try:
                sha, size = download_image(client, img["url"], dst)
                img["sha256"] = sha
                img["bytes"] = size
                img["local_path"] = str(dst.relative_to(dest.parents[5]))
            except Exception as e:
                print(f"  ! si {rec_id} image {i} download failed: {e}", flush=True)
        record["images"] = [i for i in record["images"] if i.get("local_path")]
        if not record["images"]:
            continue

        append_metadata(dest, record)
        counts[country] = counts.get(country, 0) + 1

    return counts
