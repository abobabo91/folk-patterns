"""Cleveland Museum of Art Open Access API — https://openaccess-api.clevelandart.org

No API key required, CC0-licensed metadata. Every record we accept has a real
museum-shot image on gray/black background (no colonial photography
contamination like the Rijks/Europeana pipeline has).

Coverage per our current cohort (from probes 2026-07-22):
  Cambodian 69, Burmese 61, Javanese 69, Cham 8, Thai 8, Turkmen 4,
  Tajik 3, Iban 2, Batak 1, Lao 14 (some Chinese-Liao pollution).
  Zero: Uzbek, Kazakh, Kyrgyz, Balinese, Hmong, Toraja, Filipino, Vietnamese.

Strategy: query by ethnicity name AND by demonym (Burmese vs Bamar,
Cambodian vs Khmer). Route by matching culture-string against the seed
tradition-owner map to protect against mis-attribution (some "Lao" records
are actually Chinese Liao dynasty)."""
from __future__ import annotations

import json
from typing import Any

from ..util import RateLimitedClient, download_image, append_metadata, library_path, raw_path
from ..classify import classify
from ..rejects import log_reject

BASE = "https://openaccess-api.clevelandart.org/api/artworks"


def search(client: RateLimitedClient, query: str, limit: int = 100) -> list[dict]:
    """Return raw Cleveland records with images."""
    j = client.get_json(BASE, params={"q": query, "has_image": 1, "limit": limit})
    return j.get("data") or []


def _to_canonical(rec: dict, cultural: dict) -> dict:
    """Flatten a Cleveland record into our canonical schema."""
    from ..schema import _empty_record

    oid = str(rec.get("id") or rec.get("accession_number") or "?")
    r = _empty_record("cleveland", oid)
    r["cultural"].update(cultural)

    # --- source
    r["source"]["museum_name"] = "Cleveland Museum of Art"
    r["source"]["object_url"] = rec.get("url")
    r["source"]["credit_line"] = rec.get("creditline")
    r["source"]["accession_number"] = rec.get("accession_number")
    # Cleveland is CC0 for both metadata and images
    r["source"]["rights"] = "public-domain"
    r["source"]["license_url"] = "https://creativecommons.org/publicdomain/zero/1.0/"

    # --- physical
    r["physical"]["title"] = rec.get("title")
    r["physical"]["date_text"] = rec.get("creation_date")
    r["physical"]["date_earliest"] = rec.get("creation_date_earliest")
    r["physical"]["date_latest"] = rec.get("creation_date_latest")
    r["physical"]["classification"] = rec.get("type") or (rec.get("department") or "")
    r["physical"]["medium_raw"] = rec.get("technique") or rec.get("materials")
    r["physical"]["summary"] = rec.get("description") or rec.get("tombstone")
    # Dimensions block
    dims = rec.get("dimensions") or {}
    if isinstance(dims, dict) and dims:
        # Cleveland often includes {'framed': ..., 'unframed': ...} as text
        for k, v in dims.items():
            if isinstance(v, str) and v.strip():
                r["physical"]["dimensions_note"] = v.strip()
                break

    # --- location
    culture = rec.get("culture") or []
    if isinstance(culture, list) and culture:
        r["location"]["made_in_place"] = culture[0][:200]
    r["location"]["current_museum"] = "Cleveland Museum of Art"

    # --- attribution: creators list
    creators = rec.get("creators") or []
    if isinstance(creators, list):
        r["attribution"]["makers"] = [
            {"name": c.get("description") or c.get("id"),
             "role": c.get("role"), "dates": None, "urls": []}
            for c in creators if isinstance(c, dict)
        ]

    # --- images: prefer 'web' (already sized ~1600px), else 'print', else 'full'
    imgs = rec.get("images") or {}
    if isinstance(imgs, dict):
        for i, key in enumerate(("web", "print", "full")):
            v = imgs.get(key) or {}
            url = v.get("url")
            if url:
                r["images"].append({
                    "url": url, "iiif_id": None, "iiif_base": None,
                    "role": "primary" if i == 0 else "alt",
                    "sha256": None, "bytes": None, "local_path": None,
                })
                break  # one is enough; there are more but the same object

    r["raw"] = rec
    return r


def scrape_ethnicity(
    client: RateLimitedClient,
    region: str,
    country: str,
    ethnicity: str,
    queries: list[str],
    max_per_query: int = 100,
    max_total: int = 80,
    min_ethnicity_score: int = 0,
    seed_accept_tokens: list[str] | None = None,
) -> int:
    """Search Cleveland for the ethnicity's queries, filter by culture-string
    to reject false positives (Chinese Liao dynasty appearing on Lao queries),
    download images, save canonical records."""
    from ..schema import _empty_record  # noqa

    cache_key = f"cleveland__{country.replace(' ','_')}__{ethnicity.replace(' ','_')}"
    cache = raw_path("cleveland", cache_key)
    if cache.exists():
        raw_items = json.loads(cache.read_text(encoding="utf-8"))
    else:
        raw_items = []
        seen_ids: set[str] = set()
        for q in queries:
            try:
                items = search(client, q, limit=max_per_query)
            except Exception as e:
                print(f"  ! cleveland search {q!r} failed: {e}", flush=True)
                continue
            for it in items:
                rid = str(it.get("id"))
                if not rid or rid in seen_ids:
                    continue
                seen_ids.add(rid)
                raw_items.append(it)
        cache.write_text(json.dumps(raw_items, ensure_ascii=False), encoding="utf-8")

    if not raw_items:
        return 0

    # Strict culture-string filter with word-boundary matching. Substring
    # matching was catching Chinese records on "Chin" (chin ⊂ china) and
    # generic Philippine hits on T'boli/Yakan. This version requires an
    # unambiguous whole-word token from the culture-list.
    import re
    # Country-majority ethnicities can accept country-level records ("Myanmar",
    # "burma" for Bamar). Minorities in the same country CANNOT — otherwise
    # Chin (Myanmar) would sweep up every Bamar-labelled record. Minorities
    # must be matched by their own ethnonym or a bespoke tradition term.
    MAJORITY = {
        "Uzbekistan":"Uzbek","Kazakhstan":"Kazakh","Kyrgyzstan":"Kyrgyz",
        "Turkmenistan":"Turkmen","Tajikistan":"Tajik","Indonesia":"Javanese",
        "Malaysia":"Malay","Thailand":"Thai","Vietnam":"Kinh",
        "Cambodia":"Khmer","Laos":"Lao","Myanmar":"Bamar",
        "Philippines":"Filipino",
    }
    is_majority = MAJORITY.get(country) == ethnicity
    accept_tokens = [ethnicity.lower()]
    if is_majority:
        accept_tokens += [
            country.lower(),
            {"Myanmar":"burma","Cambodia":"khmer","Vietnam":"vietnamese",
             "Indonesia":"indonesian","Uzbekistan":"uzbekistan",
             "Turkmenistan":"turkmenistan","Kazakhstan":"kazakhstan",
             "Kyrgyzstan":"kyrgyzstan","Tajikistan":"tajikistan",
             "Afghanistan":"afghanistan","Laos":"laotian","Thailand":"thailand",
             "Malaysia":"malaysian","Philippines":"philippines"}
            .get(country, "").lower(),
        ]
    else:
        # For minorities, add specific tradition terms from the seed JSON
        # (`cleveland_accept_tokens` on the ethnicity entry). Only ethnonyms +
        # close subgroup names. Broad geographic terms (mindanao, borneo,
        # sulawesi) are OMITTED because they catch neighbouring groups
        # Cleveland tags under the same geography (e.g. Mindanao includes
        # Maranao/Mandaya not just T'boli).
        accept_tokens += [t.lower() for t in (seed_accept_tokens or [])]
    accept_tokens = [t for t in accept_tokens if t and len(t) >= 3]

    # Per-country exclusion tokens — reject records that clearly belong to
    # a neighbour we DON'T want catching them.
    reject_tokens = {
        "Myanmar": ["china", "chinese", "tibet", "tibetan", "iran", "iraq", "japan"],
        "Laos": ["liao", "china", "chinese", "japan"],
        "Cambodia": ["china", "chinese", "japan"],
        "Vietnam": ["china", "chinese", "japan"],
        "Thailand": ["china", "chinese", "japan"],
        "Philippines": [],  # T'boli/Yakan queries alone should catch the hits
    }.get(country, [])

    def _matches(hay: str, token: str) -> bool:
        return re.search(r"\b" + re.escape(token) + r"s?\b", hay) is not None

    saved = 0
    # Ingest-time junk gate for Cleveland records (mostly redundant since
    # Cleveland is curator-vetted, but catches Latin binomials on the rare
    # zoological/botanical record that leaks in via cross-department search).
    from ..junk import should_reject

    # Region-level rejects + positive place tokens from the shared places.py
    # config. Short ethnonyms ("san", "chin", "aka") need BOTH signals: the
    # culture string must contain a positive in-region place token AND not
    # contain a reject-place token. Otherwise "Mexico Guerrero San Jerónimo"
    # (matches "san") and "Guatemala Quiché San Juan Cotzal" (matches "san")
    # leak through the reject-only guard.
    from ..places import REGIONS
    region_cfg = REGIONS.get(region) or {}
    region_rejects = set(region_cfg.get("reject_places") or [])
    region_places = set(region_cfg.get("place_to_country") or {})

    for it in raw_items:
        cul_str = " ".join(it.get("culture") or []).lower()
        title_str = (it.get("title") or "").lower()
        # Accept wins over reject. Culture matched first; if culture doesn't
        # match but ALSO doesn't name a rejected place, allow the title to
        # carry the record (Yakan headcloth: culture="Philippines" carries no
        # ethnicity detail, title says "Yakan seputangan"). If the record's
        # culture explicitly names another region's place ("italy, venice"),
        # only accept if culture ALSO names our target (multi-culture
        # Sogdian attributions like "sogdia (uzbekistan) china").
        # Long tokens (≥5 chars) or multi-word are trustworthy; short tokens
        # (<5) collide with common Spanish/Italian words ("San Marco", "San
        # Jerónimo", "chin strap"). Short tokens require the culture field
        # to ALSO contain a positive in-region place — otherwise Mexican and
        # Guatemalan "San Juan" pieces leak into SSA/San.
        culture_matches = [t for t in accept_tokens if _matches(cul_str, t)]
        culture_accept_long = any(len(t) >= 5 or " " in t for t in culture_matches)
        culture_accept_short = bool(culture_matches) and not culture_accept_long
        # Independent-evidence check: culture must name an in-region place
        # OTHER than the accept token itself. Otherwise "San Gabriel"
        # satisfies both accept ("san") and place ("san" → Botswana) on the
        # same word — no independent evidence.
        _accept_set = set(accept_tokens)
        culture_has_in_region_place = any(
            _matches(cul_str, p) for p in region_places if p not in _accept_set
        )
        culture_country_reject = any(_matches(cul_str, r) for r in reject_tokens)
        culture_region_reject = bool(cul_str) and any(
            _matches(cul_str, r) for r in region_rejects
        )
        any_reject = culture_country_reject or culture_region_reject
        title_matches = [t for t in accept_tokens if _matches(title_str, t)]
        title_accept_long = any(len(t) >= 5 and " " not in t for t in title_matches)

        # Rules:
        #  long accept token (≥5 chars or multi-word) is unambiguous — always
        #    keep, even if a reject token also appears in the culture string
        #    (Tang-dynasty Sogdian textiles have culture "Sogdia (Uzbekistan)
        #    China" — accept because "uzbekistan" is unambiguous).
        #  short accept token (<5 chars single-word like "san","chin","aka")
        #    is unreliable — collides with "San Marco" / "chin strap". Keep
        #    only when culture ALSO names a different in-region place
        #    (independent evidence) AND no reject-place appears.
        #  title-fallback: only for long tokens, and only when culture has
        #    NO reject-place. "Yakan seputangan" title survives when culture
        #    is bare "Philippines"; "San Marco" title dies when culture is
        #    "Italy, Venice".
        if culture_accept_long:
            pass  # unambiguous target attribution wins
        elif culture_accept_short and culture_has_in_region_place and not any_reject:
            pass  # short token AND independent in-region place, no conflict
        elif any_reject:
            log_reject(source="cleveland", reason="culture-reject-place",
                       region=region, country=country, ethnicity=ethnicity,
                       title=it.get("title"),
                       extra={"culture": cul_str[:160]})
            continue
        elif title_accept_long:
            pass  # neutral culture, title carries a distinctive long token
        else:
            log_reject(source="cleveland", reason="no-accept-match",
                       region=region, country=country, ethnicity=ethnicity,
                       title=it.get("title"),
                       extra={"culture": cul_str[:160]})
            continue
        # Junk-title gate (Latin binomials, heraldic, etc.).
        title_raw = it.get("title") or ""
        desc_raw = it.get("description") or it.get("tombstone") or ""
        is_junk, junk_reason = should_reject(title_raw, desc_raw, "Cleveland Museum of Art")
        if is_junk:
            log_reject(source="cleveland", reason=f"junk-{junk_reason}",
                       region=region, country=country, ethnicity=ethnicity,
                       title=title_raw)
            continue

        title = it.get("title") or ""
        cf = classify({"classification": it.get("type") or "",
                       "object_type": it.get("type") or "",
                       "medium": it.get("technique") or it.get("materials") or "",
                       "material_technique": "", "title": title,
                       "objectName": title,
                       "summary": it.get("description") or it.get("tombstone") or "",
                       "description": ""})
        cultural = {
            "region": region, "country": country, "ethnicity": ethnicity,
            "tradition": queries[0] if queries else ethnicity,
            "art_form": cf["art_form"], "pattern_density": cf["pattern_density"],
        }
        rec = _to_canonical(it, cultural)
        dest = library_path(region, country, ethnicity, cf["art_form"], (queries[0] if queries else ethnicity))
        for i, img in enumerate(rec["images"]):
            fname = f"cle_{rec['id'].split('-',1)[-1]}_{i}.jpg" if i else f"cle_{rec['id'].split('-',1)[-1]}.jpg"
            dst = dest / "images" / fname
            try:
                sha, size = download_image(client, img["url"], dst)
                img["sha256"] = sha
                img["bytes"] = size
                img["local_path"] = str(dst.relative_to(dest.parents[5]))
            except Exception as e:
                print(f"  ! cleveland {rec['id']} img {i} download failed: {e}", flush=True)
        rec["images"] = [i for i in rec["images"] if i.get("local_path")]
        if not rec["images"]:
            continue
        append_metadata(dest, rec)
        saved += 1
    return saved
