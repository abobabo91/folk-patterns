"""Canonical schema every downloaded object gets flattened into.

Each source museum has its own transformer that maps its native record into
this shape. The `raw` field always contains the untouched museum response so
we never re-scrape to recover a field we dropped.

Contract:
  - Every top-level section (source, cultural, physical, location, attribution,
    linked_data, images, map) is ALWAYS present.
  - Every field is either its typed value or null / [] / {}. No missing keys.
  - Dates parsed to year integers when possible; text preserved in date_text.
  - Multi-image objects carry every image URL in images[]; images[0] is primary.

The site's build_index.py consumes this shape directly.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# ------------------------------------------------------------------ museum ID map


MUSEUM_NAMES = {
    "va": "Victoria and Albert Museum",
    "met": "Metropolitan Museum of Art",
    "rijks": "Rijksmuseum",
    "cooper": "Cooper Hewitt, Smithsonian Design Museum",
    "smithsonian": "Smithsonian Institution",
    "europeana": "Europeana (aggregator)",
    "cleveland": "Cleveland Museum of Art",
    "commons_arch": "Wikimedia Commons (architecture)",
}


# ------------------------------------------------------------------ empty shell


def _empty_record(source: str, object_id: str) -> dict:
    """Every field always present so consumers never have to `if k in rec`."""
    return {
        "id": f"{source}-{object_id}",
        "source": {
            "museum": source,
            "museum_name": MUSEUM_NAMES.get(source, source),
            "object_id": object_id,
            "object_url": None,
            "iiif_manifest": None,
            "credit_line": None,
            "rights": None,          # "public-domain", "public-domain-non-commercial", "unknown"
            "license_url": None,
            "accession_number": None,
            "accession_year": None,
        },
        "cultural": {
            "region": None,
            "country": None,
            "ethnicity": None,
            "tradition": None,
            "art_form": None,
            "pattern_density": None,
        },
        "physical": {
            "title": None,
            "titles_alt": [],
            "date_text": None,
            "date_earliest": None,
            "date_latest": None,
            "period": None,
            "dynasty": None,
            "materials": [],
            "techniques": [],
            "medium_raw": None,
            "classification": None,
            "styles": [],
            "categories": [],
            "physical_description": None,
            "summary": None,
            "historical_context": None,
            "dimensions": [],           # [{dimension, value, unit}]
            "dimensions_note": None,
            "marks_inscriptions": None,
        },
        "location": {
            "made_in_place": None,
            "made_in_place_alt": [],
            "current_gallery": None,
            "current_museum": None,
            "on_display": None,
        },
        "attribution": {
            "makers": [],               # [{name, role, dates, urls}]
            "acquisition_history": [],
            "excavation": None,
        },
        "linked_data": {
            "wikidata_url": None,
            "aat_urls": [],
            "wikipedia": None,
            "other_urls": [],
        },
        "images": [],                   # [{url, iiif_id, iiif_base, role, sha256, bytes, local_path, low_quality?}]
        "map": {
            "lat": None,
            "lon": None,
            "geocoded_from": None,
        },
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "raw": None,
    }


# ---------------------------------------------------------------- utilities


def _parse_year(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"\b(1[0-9]{3}|2[0-9]{3}|[6-9][0-9]{2})\b", str(text))
    return int(m.group(1)) if m else None


def _cent_range(text: str | None) -> tuple[int | None, int | None]:
    """Loose interpretation of century + century-half tokens.
    'mid 19th century' -> (1825, 1875); '18th century' -> (1700, 1799)."""
    if not text:
        return None, None
    s = text.lower()
    m = re.search(r"(\d+)(?:st|nd|rd|th) century", s)
    if not m:
        return _parse_year(s), _parse_year(s)
    c = int(m.group(1))
    start = (c - 1) * 100
    end = start + 99
    if "early" in s:
        return start, start + 33
    if "mid" in s:
        return start + 25, start + 75
    if "late" in s:
        return start + 66, end
    return start, end


# ---------------------------------------------------------------- V&A transformer


def va_iiif_base(image_id: str) -> str:
    return f"https://framemark.vam.ac.uk/collections/{image_id}/full/{{size}}/0/default.jpg"


def from_va(search_rec: dict, deep_rec: dict | None, cultural: dict) -> dict:
    """Merge V&A search snippet + deep /museumobject record into canonical."""
    sys_num = search_rec.get("systemNumber") or (deep_rec or {}).get("systemNumber")
    r = _empty_record("va", sys_num or "?")
    r["cultural"].update(cultural)

    # -- source
    r["source"]["object_url"] = f"https://collections.vam.ac.uk/item/{sys_num}"
    r["source"]["credit_line"] = (deep_rec or {}).get("creditLine") or search_rec.get("_creditLine")
    r["source"]["accession_number"] = (deep_rec or {}).get("accessionNumber")
    r["source"]["accession_year"] = (deep_rec or {}).get("accessionYear")
    # V&A doesn't universally publish rights; assume image is CC by V&A policy
    # if it's downloadable, but stay conservative.
    r["source"]["rights"] = "unknown"

    # -- physical
    r["physical"]["title"] = search_rec.get("_primaryTitle") or None
    titles = (deep_rec or {}).get("titles") or []
    r["physical"]["titles_alt"] = [t.get("title") for t in titles if isinstance(t, dict) and t.get("title") and t.get("title") != r["physical"]["title"]]
    r["physical"]["date_text"] = search_rec.get("_primaryDate") or None
    # productionDates has parsed ranges
    prod_dates = (deep_rec or {}).get("productionDates") or []
    if prod_dates:
        d = prod_dates[0].get("date") or {}
        r["physical"]["date_earliest"] = _parse_year(d.get("earliest"))
        r["physical"]["date_latest"] = _parse_year(d.get("latest"))
        if not r["physical"]["date_text"]:
            r["physical"]["date_text"] = d.get("text")
    if r["physical"]["date_earliest"] is None:
        s, e = _cent_range(r["physical"]["date_text"])
        r["physical"]["date_earliest"] = s
        r["physical"]["date_latest"] = e
    r["physical"]["medium_raw"] = search_rec.get("materialsAndTechniques") or (deep_rec or {}).get("materialsAndTechniques")
    r["physical"]["classification"] = search_rec.get("objectType") or (deep_rec or {}).get("objectType")
    r["physical"]["categories"] = [c.get("text") for c in ((deep_rec or {}).get("categories") or []) if c.get("text")]
    r["physical"]["materials"] = [m.get("text") for m in ((deep_rec or {}).get("materials") or []) if isinstance(m, dict) and m.get("text")]
    r["physical"]["techniques"] = [t.get("text") for t in ((deep_rec or {}).get("techniques") or []) if isinstance(t, dict) and t.get("text")]
    r["physical"]["physical_description"] = (deep_rec or {}).get("physicalDescription") or None
    r["physical"]["summary"] = (deep_rec or {}).get("summaryDescription") or None
    r["physical"]["historical_context"] = (deep_rec or {}).get("historicalContext") or None
    r["physical"]["styles"] = [s.get("text") for s in ((deep_rec or {}).get("styles") or []) if isinstance(s, dict) and s.get("text")]
    dims = []
    for d in (deep_rec or {}).get("dimensions") or []:
        if not isinstance(d, dict): continue
        dims.append({
            "dimension": d.get("dimension"),
            "value": d.get("value"),
            "unit": d.get("unit"),
            "part": d.get("part") or None,
        })
    r["physical"]["dimensions"] = dims
    r["physical"]["dimensions_note"] = (deep_rec or {}).get("dimensionsNote") or None
    r["physical"]["marks_inscriptions"] = (deep_rec or {}).get("marksAndInscriptions") or None

    # -- location
    places = (deep_rec or {}).get("placesOfOrigin") or []
    if places:
        r["location"]["made_in_place"] = (places[0].get("place") or {}).get("text")
        r["location"]["made_in_place_alt"] = [
            (p.get("place") or {}).get("text") for p in places[1:] if p.get("place")
        ]
    if r["location"]["made_in_place"] is None:
        r["location"]["made_in_place"] = search_rec.get("_primaryPlace")
    gallery = (deep_rec or {}).get("galleryLocations") or []
    if gallery:
        r["location"]["current_gallery"] = (gallery[0].get("current") or {}).get("text")
    r["location"]["current_museum"] = "Victoria and Albert Museum, London"
    r["location"]["on_display"] = None  # V&A doesn't reliably expose this

    # -- attribution
    makers = []
    for m in (deep_rec or {}).get("artistMakerPeople") or []:
        if not isinstance(m, dict): continue
        makers.append({
            "name": (m.get("name") or {}).get("text"),
            "role": (m.get("association") or {}).get("text"),
            "dates": None,
            "urls": [],
        })
    for m in (deep_rec or {}).get("artistMakerOrganisations") or []:
        if not isinstance(m, dict): continue
        makers.append({
            "name": (m.get("name") or {}).get("text"),
            "role": (m.get("association") or {}).get("text"),
            "dates": None,
            "urls": [],
        })
    r["attribution"]["makers"] = makers
    r["attribution"]["acquisition_history"] = [
        oh.get("text") if isinstance(oh, dict) else str(oh)
        for oh in ((deep_rec or {}).get("objectHistory") or [])
    ]

    # -- images: primary + additional
    img_ids: list[str] = []
    primary = search_rec.get("_primaryImageId")
    if primary:
        img_ids.append(primary)
    for iid in (deep_rec or {}).get("images") or []:
        if isinstance(iid, str) and iid not in img_ids:
            img_ids.append(iid)
    for i, iid in enumerate(img_ids):
        r["images"].append({
            "url": f"https://framemark.vam.ac.uk/collections/{iid}/full/1000,/0/default.jpg",
            "iiif_id": iid,
            "iiif_base": va_iiif_base(iid),
            "role": "primary" if i == 0 else "alt",
            "sha256": None,
            "bytes": None,
            "local_path": None,
        })

    r["raw"] = {"search": search_rec, "deep": deep_rec}
    return r


# ---------------------------------------------------------------- Met transformer


def from_met(obj: dict, cultural: dict) -> dict:
    """Met's `/objects/<id>` endpoint IS the deep record; no second fetch."""
    oid = str(obj.get("objectID"))
    r = _empty_record("met", oid)
    r["cultural"].update(cultural)

    # -- source
    r["source"]["object_url"] = obj.get("objectURL")
    r["source"]["credit_line"] = obj.get("creditLine")
    r["source"]["accession_number"] = obj.get("accessionNumber")
    r["source"]["accession_year"] = obj.get("accessionYear")
    if obj.get("isPublicDomain"):
        r["source"]["rights"] = "public-domain"
        r["source"]["license_url"] = "https://creativecommons.org/publicdomain/zero/1.0/"
    else:
        r["source"]["rights"] = obj.get("rightsAndReproduction") or "unknown"

    # -- physical
    r["physical"]["title"] = obj.get("title") or None
    r["physical"]["date_text"] = obj.get("objectDate")
    r["physical"]["date_earliest"] = obj.get("objectBeginDate")
    r["physical"]["date_latest"] = obj.get("objectEndDate")
    if r["physical"]["date_earliest"] is None:
        s, e = _cent_range(r["physical"]["date_text"])
        r["physical"]["date_earliest"] = s
        r["physical"]["date_latest"] = e
    r["physical"]["period"] = obj.get("period") or None
    r["physical"]["dynasty"] = obj.get("dynasty") or None
    r["physical"]["medium_raw"] = obj.get("medium") or None
    r["physical"]["classification"] = obj.get("classification") or None
    # Met has no separate materials array, but medium is a semicolon list
    if r["physical"]["medium_raw"]:
        # split on ";" or ","
        parts = [p.strip() for p in re.split(r"[;,]", r["physical"]["medium_raw"]) if p.strip()]
        r["physical"]["materials"] = parts[:8]
    dim_text = obj.get("dimensions") or obj.get("measurements")
    if dim_text:
        r["physical"]["dimensions_note"] = str(dim_text) if not isinstance(dim_text, str) else dim_text

    # -- location
    place_bits = [
        obj.get("city"), obj.get("state"), obj.get("subregion"),
        obj.get("region"), obj.get("country"),
    ]
    place_bits = [p for p in place_bits if p]
    r["location"]["made_in_place"] = ", ".join(place_bits) if place_bits else obj.get("culture")
    r["location"]["current_gallery"] = obj.get("GalleryNumber") or None
    r["location"]["current_museum"] = obj.get("repository") or "Metropolitan Museum of Art, New York"
    r["location"]["on_display"] = bool(obj.get("GalleryNumber"))

    # -- attribution
    if obj.get("artistDisplayName"):
        r["attribution"]["makers"] = [{
            "name": obj.get("artistDisplayName"),
            "role": obj.get("artistRole"),
            "dates": (
                f"{obj.get('artistBeginDate') or '?'} – {obj.get('artistEndDate') or '?'}"
                if obj.get("artistBeginDate") else None
            ),
            "urls": [
                u for u in (obj.get("artistWikidata_URL"), obj.get("artistULAN_URL")) if u
            ],
        }]
    if obj.get("excavation"):
        r["attribution"]["excavation"] = obj.get("excavation")

    # -- linked data
    if obj.get("objectWikidata_URL"):
        r["linked_data"]["wikidata_url"] = obj.get("objectWikidata_URL")
    r["linked_data"]["aat_urls"] = [
        t.get("AAT_URL") for t in (obj.get("tags") or []) if isinstance(t, dict) and t.get("AAT_URL")
    ]

    # -- images
    if obj.get("primaryImage") or obj.get("primaryImageSmall"):
        r["images"].append({
            "url": obj.get("primaryImage") or obj.get("primaryImageSmall"),
            "iiif_id": None,
            "iiif_base": None,
            "role": "primary",
            "sha256": None,
            "bytes": None,
            "local_path": None,
        })
    for i, url in enumerate(obj.get("additionalImages") or []):
        r["images"].append({
            "url": url,
            "iiif_id": None,
            "iiif_base": None,
            "role": "alt",
            "sha256": None,
            "bytes": None,
            "local_path": None,
        })

    r["raw"] = obj
    return r


# --------------------------------------------------- Rijksmuseum (Linked Art)


def from_smithsonian(row: dict, cultural: dict) -> dict:
    """Smithsonian Open Access API (EDAN) — https://api.si.edu/openaccess/api/v1.0/

    Rows come from `/search` results. Nested shape:
      row.content.descriptiveNonRepeating (title, unit, guid, image URLs)
      row.content.freetext (labeled key/value pairs: date, place, notes, etc.)
      row.content.indexedStructured (normalized: place, date buckets, geoLocation)
    """
    oid = row.get("id") or row.get("url") or "?"
    r = _empty_record("smithsonian", oid)
    r["cultural"].update(cultural)

    content = row.get("content") or {}
    dnr = content.get("descriptiveNonRepeating") or {}
    ft = content.get("freetext") or {}
    ix = content.get("indexedStructured") or {}

    def _labeled(key: str) -> str | None:
        """Freetext entries are lists of {label, content}. Return first content."""
        arr = ft.get(key) or []
        if arr and isinstance(arr[0], dict):
            return arr[0].get("content")
        return None

    def _labeled_all(key: str) -> list[str]:
        arr = ft.get(key) or []
        return [x.get("content") for x in arr if isinstance(x, dict) and x.get("content")]

    # -- source
    r["source"]["museum_name"] = _labeled("dataSource") or "Smithsonian Institution"
    r["source"]["object_url"] = dnr.get("record_link") or (
        f"https://collections.si.edu/search/detail/edanmdm:{dnr.get('record_ID')}"
        if dnr.get("record_ID") else None
    )
    r["source"]["credit_line"] = _labeled("creditLine")
    r["source"]["accession_number"] = _labeled("identifier")
    rights = _labeled("objectRights") or ""
    if "CC0" in rights:
        r["source"]["rights"] = "public-domain"
        r["source"]["license_url"] = "https://creativecommons.org/publicdomain/zero/1.0/"
    else:
        r["source"]["rights"] = rights or "unknown"

    # -- physical
    r["physical"]["title"] = (dnr.get("title") or {}).get("content") or row.get("title")
    r["physical"]["date_text"] = _labeled("date")
    dates_ix = ix.get("date") or []
    if dates_ix:
        # e.g. ["1800s", "1890s"]  -> pick the earliest 4-digit year and latest
        years = []
        for d in dates_ix:
            y = _parse_year(d)
            if y is not None:
                years.append(y)
        if years:
            r["physical"]["date_earliest"] = min(years)
            r["physical"]["date_latest"] = max(years)
    if r["physical"]["date_earliest"] is None:
        s, e = _cent_range(r["physical"]["date_text"])
        r["physical"]["date_earliest"] = s
        r["physical"]["date_latest"] = e

    r["physical"]["classification"] = _labeled("objectType")
    r["physical"]["categories"] = _labeled_all("objectType")
    r["physical"]["physical_description"] = _labeled("notes")
    r["physical"]["medium_raw"] = _labeled("physicalDescription")
    # Some records include Dimensions inside a second physicalDescription entry.
    for pd in _labeled_all("physicalDescription"):
        if pd and ("cm" in pd or "in." in pd):
            r["physical"]["dimensions_note"] = pd
            break

    # -- location
    r["location"]["made_in_place"] = _labeled("place")
    r["location"]["current_museum"] = r["source"]["museum_name"]

    # -- attribution
    if _labeled("name"):
        r["attribution"]["makers"] = [
            {"name": n, "role": None, "dates": None, "urls": []}
            for n in _labeled_all("name")
        ]

    # -- linked data
    guid = dnr.get("guid")
    if guid:
        r["linked_data"]["other_urls"] = [guid]

    # -- images
    om = dnr.get("online_media") or {}
    media = om.get("media") or []
    for i, m in enumerate(media):
        if not isinstance(m, dict):
            continue
        url = m.get("content") or m.get("thumbnail")
        if not url:
            continue
        r["images"].append({
            "url": url,
            "iiif_id": None,
            "iiif_base": None,
            "role": "primary" if i == 0 else "alt",
            "sha256": None,
            "bytes": None,
            "local_path": None,
        })

    r["raw"] = row
    return r


def from_rijks_linked_art(obj: dict, cultural: dict) -> dict:
    """Rijksmuseum's new API returns Linked Art JSON. Extract flat fields."""
    lod_id = obj.get("id") or ""
    oid = lod_id.rsplit("/", 1)[-1] if lod_id else "?"
    r = _empty_record("rijks", oid)
    r["cultural"].update(cultural)

    # -- source
    r["source"]["object_url"] = lod_id
    r["source"]["rights"] = "public-domain"  # Rijks default
    r["source"]["license_url"] = "https://creativecommons.org/publicdomain/mark/1.0/"

    # -- title (from identified_by[type=Name])
    for id_by in obj.get("identified_by") or []:
        if isinstance(id_by, dict) and id_by.get("type") == "Name" and id_by.get("content"):
            r["physical"]["title"] = id_by["content"]
            break

    # -- classifications -> categories
    r["physical"]["categories"] = [
        c.get("_label") for c in obj.get("classified_as") or [] if isinstance(c, dict) and c.get("_label")
    ]
    if r["physical"]["categories"]:
        r["physical"]["classification"] = r["physical"]["categories"][0]

    # -- production (dates + place)
    prod = obj.get("produced_by") or {}
    if isinstance(prod, dict):
        for part in prod.get("part") or []:
            ts = (part.get("timespan") or {}).get("identified_by") or []
            for id_by in ts:
                if isinstance(id_by, dict) and id_by.get("content"):
                    r["physical"]["date_text"] = id_by["content"]
                    r["physical"]["date_earliest"] = _parse_year(part.get("timespan", {}).get("begin_of_the_begin"))
                    r["physical"]["date_latest"] = _parse_year(part.get("timespan", {}).get("end_of_the_end"))
                    break
            place = part.get("took_place_at") or []
            if place:
                r["location"]["made_in_place"] = place[0].get("_label")

    # -- materials
    r["physical"]["materials"] = [
        m.get("_label") for m in obj.get("made_of") or [] if isinstance(m, dict) and m.get("_label")
    ]

    # -- image URL
    for rep in obj.get("representation") or []:
        if not isinstance(rep, dict): continue
        for ap in rep.get("access_point") or []:
            if isinstance(ap, dict) and ap.get("id"):
                r["images"].append({
                    "url": ap["id"],
                    "iiif_id": None,
                    "iiif_base": None,
                    "role": "primary" if not r["images"] else "alt",
                    "sha256": None,
                    "bytes": None,
                    "local_path": None,
                })
                break

    r["location"]["current_museum"] = "Rijksmuseum, Amsterdam"
    r["raw"] = obj
    return r
