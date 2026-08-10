"""Rijksmuseum new Search API — https://data.rijksmuseum.nl/

NO API KEY required — the new Linked Art Search API is fully public.

  Search: GET /search/collection?description=<term>&imageAvailable=true
          Returns 100 results per page as LOD identifiers
  Resolve: GET https://id.rijksmuseum.nl/<id>  (with Accept: application/ld+json)
           Returns Linked Art JSON with title, creator, dates, materials, etc.

Coverage note (2026-07-18): Rijksmuseum has near-zero Central Asian holdings
(Uzbekistan/Bukhara/Turkestan queries return 0). Dutch colonial reach was to
Indonesia — this scraper is high-ROI for SE Asia (Sumatra 1861, Java 1645,
batik 40, sarong 17, ikat 10) but not useful for the current Central Asia region.

Wired in but not currently invoked from scrape_region for central-asia.
"""
from __future__ import annotations

import json
from typing import Any

from ..util import RateLimitedClient, download_image, append_metadata, library_path, raw_path
from ..classify import classify
from ..schema import from_rijks_linked_art

BASE = "https://data.rijksmuseum.nl"


def search(client: RateLimitedClient, description: str | None = None,
           technique: str | None = None, type_: str | None = None,
           material: str | None = None,
           image_available: bool = True,
           max_pages: int = 5) -> list[str]:
    """Return the list of LOD IDs (`id.rijksmuseum.nl/<n>`) matching the query."""
    params: dict = {}
    if description: params["description"] = description
    if technique: params["technique"] = technique
    if type_: params["type"] = type_
    if material: params["material"] = material
    if image_available: params["imageAvailable"] = "true"

    out: list[str] = []
    url = f"{BASE}/search/collection"
    for page in range(max_pages):
        j = client.get_json(url, params=params)
        for item in j.get("orderedItems") or []:
            if item.get("id"):
                out.append(item["id"])
        next_ = j.get("next", {}).get("id")
        if not next_ or next_ == url:
            break
        url = next_
        params = {}  # next URL already has pageToken embedded
    return out


def fetch_object(client: RateLimitedClient, lod_id: str) -> dict | None:
    """Resolve an id.rijksmuseum.nl URI to Linked Art JSON."""
    try:
        r = client.get(
            lod_id, headers={"Accept": "application/ld+json"},
        )
        return r.json()
    except Exception:
        return None


def _extract_fields(obj: dict) -> dict:
    """Distill relevant fields from Linked Art. Linked Art is verbose;
    we pull title, made-of materials, dates, and image URLs."""
    out: dict[str, Any] = {}
    out["title"] = None
    for id_by in obj.get("identified_by", []) or []:
        if id_by.get("type") == "Name" and id_by.get("content"):
            out["title"] = id_by["content"]
            break
    # Production: dates + place. Rijks stores classification-label in the
    # Dutch/English `notation` array on the classified_as entries — walk
    # those too so `object_type` isn't blank for records that lack _label.
    prod = obj.get("produced_by") or {}
    if isinstance(prod, dict):
        parts = prod.get("part") or []
        if parts:
            out["date"] = parts[0].get("timespan", {}).get("identified_by", [{}])[0].get("content")
            for part in parts:
                took_place_at = part.get("took_place_at") or []
                if took_place_at:
                    out["place"] = took_place_at[0].get("_label")
                    break
    # Rijks new API rarely populates took_place_at. When empty, dig into the
    # `referred_to_by` description text, which routinely names cities and
    # countries ("affiche tentoonstelling Semarang, Java, Indonesië…"). Also
    # concatenate the title, which sometimes carries the geo hint too.
    if not out.get("place"):
        desc_bits: list[str] = []
        for rr in obj.get("referred_to_by") or []:
            if isinstance(rr, dict) and rr.get("content"):
                desc_bits.append(rr["content"])
        if out.get("title"):
            desc_bits.append(out["title"])
        if desc_bits:
            out["place_text"] = " · ".join(desc_bits)[:1500]
    # Materials
    mats = []
    for made in obj.get("made_of") or []:
        if made.get("_label"):
            mats.append(made["_label"])
    if mats:
        out["material"] = ", ".join(mats)
    # Classification / type — look at _label AND notation[@language=en].
    # Rijks new API often omits _label but always populates notation.
    types = []
    for c in obj.get("classified_as") or []:
        if c.get("_label"):
            types.append(c["_label"])
            continue
        for note in c.get("notation") or []:
            if isinstance(note, dict) and note.get("@language") == "en" and note.get("@value"):
                types.append(note["@value"])
                break
    if types:
        out["object_type"] = ", ".join(types)
    # `shows` -> VisualItem id (need separate fetch to resolve to image URL).
    for s in obj.get("shows") or []:
        if isinstance(s, dict) and s.get("id"):
            out["visual_item_id"] = s["id"]
            break
    # Legacy `representation` field (old API returned it inline).
    rep = obj.get("representation") or []
    for r in rep:
        if r.get("access_point"):
            for ap in r["access_point"]:
                if ap.get("id"):
                    out["image_url"] = ap["id"]
                    break
        if out.get("image_url"):
            break
    return out


def _resolve_image_url(client: RateLimitedClient, visual_item_id: str) -> str | None:
    """Follow the Linked Art chain to the concrete image URL.

        object.shows[].id           ->  VisualItem
        VisualItem.digitally_shown_by[].id  ->  DigitalObject
        DigitalObject.access_point[].id     ->  https://iiif.micr.io/.../default.jpg
    """
    vi = fetch_object(client, visual_item_id)
    if not vi:
        return None
    for dig in vi.get("digitally_shown_by") or []:
        if not (isinstance(dig, dict) and dig.get("id")):
            continue
        do = fetch_object(client, dig["id"])
        if not do:
            continue
        for ap in do.get("access_point") or []:
            if isinstance(ap, dict) and ap.get("id"):
                return ap["id"]
    return None


# Bare-token traditions that collide with common Dutch words and produce
# floods of Dutch numismatic / European art records instead of the intended
# Southeast-Asian textile. "hol" (Khmer silk ikat) is Dutch for "cave/hollow"
# and shows up in coin descriptions ("holle munt", "in het hol"). Skip.
_BLOCKED_RIJKS_TRADITIONS = {"hol"}


def scrape_tradition_routed(
    client: RateLimitedClient,
    region: str,
    tradition_name: str,
    tradition_ethnicity_by_country: dict[str, str],
    max_per_country: int = 40,
) -> dict[str, int]:
    """Tradition-first scraper matching the V&A / Smithsonian signature.
    Query Rijksmuseum's description field with the tradition name, then route
    each resolved object by its place field via places.py."""
    from ..places import route_place_to_country

    if tradition_name.strip().lower() in _BLOCKED_RIJKS_TRADITIONS:
        print(f"  [rijks] skipping blocked tradition {tradition_name!r} (Dutch-word collision)", flush=True)
        return {}

    key = f"rijks__{tradition_name}"
    cache = raw_path("rijks", key)
    if cache.exists():
        lod_ids = json.loads(cache.read_text(encoding="utf-8"))
    else:
        lod_ids = search(client, description=tradition_name)
        cache.write_text(json.dumps(lod_ids), encoding="utf-8")

    if not lod_ids:
        return {}

    counts: dict[str, int] = {}
    for lod_id in lod_ids:
        obj = fetch_object(client, lod_id)
        if not obj:
            continue
        fields = _extract_fields(obj)
        # New API doesn't put image URL on the object itself — chase the
        # VisualItem chain if inline `representation` was empty.
        if not fields.get("image_url") and fields.get("visual_item_id"):
            fields["image_url"] = _resolve_image_url(client, fields["visual_item_id"])
        if not fields.get("image_url"):
            continue
        # Route by structured place first, then by any place token found
        # in the description prose. Rijks new API rarely fills took_place_at
        # but the description often names cities ("Semarang, Java, Indonesië").
        place = fields.get("place") or ""
        country = route_place_to_country(region, place, fields.get("object_type", ""))
        if country is None and fields.get("place_text"):
            from ..places import REGIONS
            region_places = (REGIONS.get(region) or {}).get("place_to_country") or {}
            region_rejects = (REGIONS.get(region) or {}).get("reject_places") or set()
            desc_l = fields["place_text"].lower()
            # First check for a reject-place. If the description names a
            # rejected country, drop even if it also names an in-region one.
            for rej in region_rejects:
                if rej in desc_l:
                    country = None
                    break
            else:
                for token, ctry in region_places.items():
                    if token in desc_l:
                        country = ctry
                        break
        if country is None:
            continue
        if counts.get(country, 0) >= max_per_country:
            continue

        # Fall back to _regional (not country-name-as-ethnicity, which produces
        # folder paths like `indonesia/indonesia/` and confuses the site index).
        ethnicity = tradition_ethnicity_by_country.get(country, "_regional")

        cf = classify({
            "classification": fields.get("object_type", ""),
            "object_type": fields.get("object_type", ""),
            "material_technique": fields.get("material", ""),
            "title": fields.get("title", "") or "",
        })
        cultural = {
            "region": region,
            "country": country,
            "ethnicity": ethnicity,
            "tradition": tradition_name,
            "art_form": cf["art_form"],
            "pattern_density": cf["pattern_density"],
        }
        record = from_rijks_linked_art(obj, cultural)
        # from_rijks_linked_art reads the object's inline `representation`
        # which is null on the new API — patch the resolved URL in.
        if not record["images"] and fields.get("image_url"):
            record["images"].append({
                "url": fields["image_url"], "iiif_id": None, "iiif_base": None,
                "role": "primary", "sha256": None, "bytes": None, "local_path": None,
            })
        dest = library_path(region, country, ethnicity, cf["art_form"], tradition_name)
        sys_num = lod_id.rsplit("/", 1)[-1]
        for i, img in enumerate(record["images"]):
            role = img["role"]
            filename = f"rijks_{sys_num}" + (f"_{i}" if role != "primary" else "") + ".jpg"
            dst = dest / "images" / filename
            try:
                sha, size = download_image(client, img["url"], dst)
                img["sha256"] = sha
                img["bytes"] = size
                img["local_path"] = str(dst.relative_to(dest.parents[5]))
            except Exception as e:
                print(f"  ! rijks {sys_num} image {i} download failed: {e}", flush=True)
        record["images"] = [i for i in record["images"] if i.get("local_path")]
        if not record["images"]:
            continue
        append_metadata(dest, record)
        counts[country] = counts.get(country, 0) + 1

    return counts
