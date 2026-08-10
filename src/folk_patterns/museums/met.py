"""Met Museum Open Access API — https://metmuseum.github.io/

Strategy: query by COUNTRY / CULTURE (which the Met's search does understand),
subtract the ~128 fallback IDs, then for each remaining object CLASSIFY it into
an art_form (textile / ceramic / jewelry / etc.) and file it under
<country>/<ethnicity>/<art_form>/general/ with the classification result
attached as metadata.

See `tools/knowledge base/museum open access apis 2026-07.md` for the
fallback-set bug.
"""
from __future__ import annotations

from ..util import RateLimitedClient, download_image, append_metadata, library_path, raw_path
from ..classify import classify
from ..schema import from_met
from ..junk import should_reject
from ..rejects import log_reject
import json

BASE = "https://collectionapi.metmuseum.org/public/collection/v1"

FALLBACK_SIGNATURE_TOTAL = {128, 129}
FALLBACK_TOP_ID = 551786
_FALLBACK_ID_CACHE: set[int] | None = None


def _get_fallback_ids(client: RateLimitedClient) -> set[int]:
    global _FALLBACK_ID_CACHE
    if _FALLBACK_ID_CACHE is not None:
        return _FALLBACK_ID_CACHE
    j = client.get_json(f"{BASE}/search", params={"q": "xzxzxzxzxznonsense", "hasImages": "true"})
    _FALLBACK_ID_CACHE = set(j.get("objectIDs") or [])
    return _FALLBACK_ID_CACHE


def _is_fallback_response(total: int, ids: list[int]) -> bool:
    return (total in FALLBACK_SIGNATURE_TOTAL) and bool(ids) and ids[0] == FALLBACK_TOP_ID


def search_ids_by_place(client: RateLimitedClient, place_or_culture: str) -> list[int]:
    j = client.get_json(f"{BASE}/search", params={"q": place_or_culture, "hasImages": "true"})
    ids = j.get("objectIDs") or []
    total = j.get("total") or 0
    if _is_fallback_response(total, ids):
        return []
    fallback = _get_fallback_ids(client)
    return [i for i in ids if i not in fallback]


def fetch_object(client: RateLimitedClient, object_id: int) -> dict | None:
    try:
        return client.get_json(f"{BASE}/objects/{object_id}")
    except Exception:
        return None


def scrape_country(
    client: RateLimitedClient,
    region: str,
    country: str,
    ethnicity_primary: str,
    country_query_terms: list[str],
    country_gate: list[str],
    max_per_art_form: int = 40,
    progress: bool = True,
) -> dict[str, int]:
    """Query Met by all country_query_terms, subtract fallback, then for each
    real hit that passes the country_gate check, classify into art_form and
    save under <country>/<ethnicity_primary>/<art_form>/general/.

    country_gate: list of substrings that MUST appear in an object's
    culture/country/region fields for us to accept it. This kills the false
    positives where a Persian object came back for a Timurid/Bukhara query.

    Returns {art_form: count_saved}.
    """
    seen_ids: set[int] = set()
    for q in country_query_terms:
        ids = search_ids_by_place(client, q)
        if progress:
            print(f"    met search q={q!r:20} -> {len(ids)} ids", flush=True)
        seen_ids.update(ids)
    if progress:
        print(f"    met unique ids: {len(seen_ids)}", flush=True)

    counts: dict[str, int] = {}
    gate_lower = [g.lower() for g in country_gate]
    processed = 0
    for oid in sorted(seen_ids):
        processed += 1
        if progress and processed % 25 == 0:
            print(f"    met progress: {processed}/{len(seen_ids)}", flush=True)
        obj = fetch_object(client, oid)
        if not obj:
            continue
        img_url = obj.get("primaryImage") or obj.get("primaryImageSmall")
        if not img_url:
            continue

        # country gate
        geo_haystack = " ".join(
            str(obj.get(k, "") or "")
            for k in ("culture", "country", "region", "subregion", "geographyType")
        ).lower()
        if not any(g in geo_haystack for g in gate_lower):
            continue

        # Scope: drop pre-Common-Era archaeology.
        end_date = obj.get("objectEndDate")
        if isinstance(end_date, int) and end_date < 0:
            log_reject(source="met", reason="pre-CE-archaeology",
                       region=region, country=country, title=obj.get("title"),
                       extra={"date_latest": end_date})
            continue

        # Junk-title gate (coins, coats of arms, camera dumps, book excerpts).
        title_str = obj.get("title") or ""
        desc_str = obj.get("objectName") or ""
        is_junk, junk_reason = should_reject(title_str, desc_str, "Metropolitan Museum of Art")
        if is_junk:
            log_reject(source="met", reason=f"junk-{junk_reason}",
                       region=region, country=country, title=title_str)
            continue

        # classify
        classify_input = {
            "classification": obj.get("classification") or "",
            "medium": obj.get("medium") or "",
            "title": obj.get("title") or "",
            "objectName": obj.get("objectName") or "",
        }
        cf = classify(classify_input)
        art_form = cf["art_form"]
        if counts.get(art_form, 0) >= max_per_art_form:
            continue

        cultural = {
            "region": region,
            "country": country,
            "ethnicity": ethnicity_primary,
            "tradition": "general",
            "art_form": art_form,
            "pattern_density": cf["pattern_density"],
        }
        record = from_met(obj, cultural)

        # Download primary + additional images
        dest = library_path(region, country, ethnicity_primary, art_form, "general")
        for i, img in enumerate(record["images"]):
            role = img["role"]
            filename = f"met_{oid}" + (f"_{i}" if role != "primary" else "") + ".jpg"
            dst = dest / "images" / filename
            try:
                sha, size = download_image(client, img["url"], dst)
                img["sha256"] = sha
                img["bytes"] = size
                img["local_path"] = str(dst.relative_to(dest.parents[5]))
            except Exception as e:
                print(f"  ! met {oid} image {i} download failed: {e}", flush=True)

        record["images"] = [i for i in record["images"] if i.get("local_path")]
        if not record["images"]:
            continue

        append_metadata(dest, record)
        counts[art_form] = counts.get(art_form, 0) + 1

    return counts
