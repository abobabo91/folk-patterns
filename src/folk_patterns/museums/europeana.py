"""Europeana Record & Search API — https://apis.europeana.eu/

Aggregator over ~4,000 European cultural-heritage institutions (museums,
libraries, archives). Uses our personal API key stored in vault. Especially
strong for ethnic-material coverage of places where former European colonial
powers collected — Rijksmuseum's Indonesian holdings surface here too, plus
independent museums (Musée du Quai Branly, Naprstek Museum, KIT/Tropenmuseum).

Filtering strategy:
  - `media=true` — only records that have a preview image
  - `reusability=open|permission` — only records whose rights allow reuse
    (drops "private" records that block hotlinking)
  - `qf` (query filter) narrows by TYPE, when useful

Every record carries `edmPreview` (thumbnail URL, hotlinkable) — we always
save that. `edmIsShownBy` (full-res, may be hotlink-blocked) is preferred
when present."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..util import RateLimitedClient, download_image, append_metadata, library_path, raw_path
from ..classify import classify
from ..places import route_place_to_country, REGIONS
from ..rejects import log_reject

BASE = "https://api.europeana.eu/record/v2"


def _collect_geo_tokens(item: dict) -> list[str]:
    """Every string in every field where Europeana might name the object's
    place of origin. We route each token separately through places.py; the
    record is accepted if ANY token routes to the target country."""
    out: list[str] = []

    def _walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            for x in v: _walk(x)
        elif isinstance(v, dict):
            for x in v.values(): _walk(x)

    for k in (
        "dcCoverage", "dcCoverageLangAware", "edmPlaceLabel",
        "dcSubject", "dcSubjectLangAware", "dcSpatial",
        "country",  # record-provider country (weakest signal; last resort)
        "title", "dcTitleLangAware",  # sometimes titles name the origin
        "dcDescription",
    ):
        _walk(item.get(k))
    return out


def _record_matches_target_country(item: dict, region: str, target_country: str) -> bool:
    """True iff at least one geo token in the record routes to `target_country`
    (or `_regional`) under the region's place_to_country map.

    Requires positive geographic evidence — Europeana returns cross-language
    tag matches that can score high on relevance while actually being about
    an unrelated place (Estonian fish photo scoring on a stray token, German
    academic book about Herero genocide filed under Namibia because "Herero"
    is a subject). Positive geo evidence is what distinguishes a real
    Wereldculturen Indonesian batik from noise."""
    from ..places import REGIONS
    reject_places = set((REGIONS.get(region) or {}).get("reject_places") or [])
    tokens = _collect_geo_tokens(item)
    saw_target = False
    saw_reject = False
    for tok in tokens:
        if not isinstance(tok, str): continue
        tl = tok.strip().lower()
        # Explicit-reject match wins (Estonian record explicitly tagged
        # dcCoverage="Estonia" AND has some SE Asia keyword in title).
        if tl in reject_places:
            saw_reject = True
            continue
        routed = route_place_to_country(region, tok, "")
        if routed == target_country or routed == "_regional":
            saw_target = True
    if saw_target and not saw_reject:
        return True
    return False

# Reject providers that catalog non-cultural specimens (plants, animals,
# minerals, insects). Europeana's `media=true` doesn't distinguish cultural
# artefacts from natural-history photos — a search for "Dayak" surfaces fern
# herbarium sheets from Naturalis alongside real Dayak textiles. Substring
# match on the dataProvider label.
NON_CULTURAL_PROVIDER_TOKENS = (
    "natural history", "biodiversity", "botanic", "botanical", "zoolog",
    "geolog", "mineral", "entomolog", "herbari", "paleontolog", "ornitholog",
    "insect", "flora", "fauna", "specimen",
    # Library / archive scans (Bodleian Swahili grammar books etc). These
    # are bibliographic records, not folk material culture.
    "libraries", "bibliothek", "biblioteca", "bibliothèque",
    "national library", "university library", "state archive",
    # Academic research repositories — full-text PDFs of papers, not artifacts.
    "ssoar", "gesis", "leibniz institute for the social",
    "polytechnic university", "open access repository",
)


def _is_cultural_provider(name: str | None) -> bool:
    if not name:
        return True   # unknown — don't drop
    lo = name.lower()
    return not any(tok in lo for tok in NON_CULTURAL_PROVIDER_TOKENS)


def _first(seq, default=None):
    """First element of a list, or the value itself if it's a scalar string.

    Historical bug: this used to return `default` for any non-list input, so
    Europeana records with `type: "SOUND"` (a plain string, not a list) had
    their edmType detected as None, letting audio records slip past the
    IMAGE-only filter. See _refresh_tiny_europeana history for the fallout."""
    if isinstance(seq, list) and seq:
        return seq[0]
    if isinstance(seq, str):
        return seq
    return default


def _get_text(item: dict, *keys: str) -> str:
    """Concatenate the first string value from each named field, for scoring."""
    parts: list[str] = []
    for k in keys:
        v = item.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    parts.append(x)
        elif isinstance(v, dict):
            # LangAware dicts like {"en": ["..."]} — walk one level
            for _, sub in v.items():
                if isinstance(sub, list):
                    parts.extend(s for s in sub if isinstance(s, str))
                elif isinstance(sub, str):
                    parts.append(sub)
    return " ".join(parts)


# Anti-topic reject patterns for ambiguous short-ethnonym false positives.
# "San" ⊂ Italian saint names (San Marco, San Giovanni, San Lorenzo…) — every
# Renaissance church in Europeana matches the "San" query. "Chin" ⊂ "Chinese"
# and "China" — 19th-century atlases of Imperio Chino matched. Add new
# ambiguous ethnonyms here as they surface. Kept in code (not seed) because
# these are cross-cutting linguistic issues, not per-culture data.
_AMBIGUOUS_ETHNONYM_REJECT: dict[str, list[str]] = {
    "san": [
        r"\bSan (Marco|Marc|Giovanni|Lorenzo|Martin|Sebasti|Isidro|Gimignano|"
        r"Zeno|Zanipol|Michele|Francesco|Salvatore|Matteo|Gennaro|Zanobi|"
        r"Simeone|Luca|Juan|Miguel|Antonio|Pedro|Pablo|Nicol|Cristob|Andr|"
        r"Bernardin)\b",
        r"\b(Iglesia|Kirche|Cathedral|Baptister|Basilica|Duomo|Scuola|"
        r"Convent|Palazzo|Monaste|Chapel|Escorial|Verrocchio|Donatello|"
        r"Cimabue|Goya|Guardi)\b",
        r"\b(Venedig|Florenz|Antequera|Neapel|Rom|Venice|Florence|Naples|"
        r"Rome|Ecija)\b",
    ],
    "chin": [
        r"\b(Imperio Chino|Chinese Empire|Peking|Beijing|Ming dynasty|"
        r"Qing dynasty|Han dynasty)\b",
        # Voortrekker(svrouwen), Voortrekkers — Afrikaans compounds, no word-end boundary.
        r"\bVoortrekker",
        r"\bChina y (Japon|Jap[oó]n)\b",
        # WWII archive shorthand: "am.-eng.-chin." for American-English-Chinese soldiers.
        r"\bchin\.[- ]",
        r"\ballied soldiers|geallieerde soldaten",
    ],
    "cham": [
        # 19th-century French cartoonist "Cham" (Amédée-Charles-Henri de Noé),
        # author of humorous illustrated books. His pen-name matches ethnonym.
        r"\bpar CHAM\b|CHAM\s*$",
        r"\bAmédée.*Noé|de No[eé], Am[eé]d",
        # Angkor Thom Bayon reliefs are Khmer, not Cham (opposing kingdoms).
        r"\bAngkor Thom|Angkor Wat|Bayon\b",
    ],
}
_AMBIGUOUS_ETHNONYM_REJECT_COMPILED: dict[str, list] = {
    k: [re.compile(p, re.I) for p in v]
    for k, v in _AMBIGUOUS_ETHNONYM_REJECT.items()
}


def _is_ambiguous_false_positive(item: dict, ethnicity: str) -> bool:
    """True iff item title/description contains anti-topic tokens for this
    ethnonym (e.g. "Iglesia de San Martin" for a "San" search)."""
    slug = ethnicity.lower().replace(" ", "-").replace("'", "")
    patterns = _AMBIGUOUS_ETHNONYM_REJECT_COMPILED.get(slug)
    if not patterns:
        return False
    hay = " ".join([
        _first(item.get("title")) or "",
        _first(item.get("dcDescription")) or "",
        _first(item.get("dcSubject")) or "",
    ])
    return any(p.search(hay) for p in patterns)


# Per-ethnicity provider blocklists for language-collision cases. Bamar's
# query "kalaga" (Burmese wall-hanging) happens to be the Estonian
# comitative case "kalaga" ("with fish"), so every Estonian institution
# with a fish still-life matched. Widened to any provider mentioning
# Estonia/Estonian/Eesti — safer than an explicit museum-by-museum list.
# Maasai gets Kunstpalast Düsseldorf ukiyo-e prints via unclear collision.
_HOSTILE_PROVIDER_BY_ETHNICITY = {
    "bamar": ["estonia", "estonian", "eesti", "tartu", "haapsalu",
              "tallinn", "pärnu"],
    "maasai": ["kunstpalast"],
}


def _is_provider_collision(item: dict, ethnicity: str) -> bool:
    slug = ethnicity.lower().replace(" ", "-").replace("'", "")
    hostile = _HOSTILE_PROVIDER_BY_ETHNICITY.get(slug) or []
    if not hostile:
        return False
    provider = (_first(item.get("dataProvider")) or _first(item.get("provider")) or "").lower()
    return any(h in provider for h in hostile)


# Generic title patterns for records that are museum catalog entries,
# cross-culture Surinamese/Caribbean material, or commercial ephemera.
_GENERIC_NONARTIFACT_TITLE = re.compile(
    r"^\s*("
    r"f[oö]rteckning|register"                                    # museum inventory
    r"|Model van een|Surinaams|Suriname|Kreools|Creoolse"          # Surinamese-Dutch scarves
    r"|How [A-Z].*learnt|Colaboraci[oó]n art"                      # academic papers, art projects
    r"|karamellipaperi|makeisk|Reklamma|Namn[aä]"                  # commercial packaging
    r")",
    re.I,
)


def _is_generic_nonartifact(item: dict) -> bool:
    title = _first(item.get("title")) or ""
    return bool(_GENERIC_NONARTIFACT_TITLE.match(title))


def _score_relevance(item: dict, ethnicity: str, seed_traditions: list[str] | None = None) -> float:
    """Score how relevant one Europeana record is to a specific ethnicity.

    Positive signals:
      +5  ethnicity name in title
      +2  ethnicity name in description / subject / creator
      +3  any seed tradition name in title
      +1  any seed tradition name in description
      +1  provider is a known ethnographic museum

    Negative signal:
      -10 title contains an obvious noise token (specimen, herbarium)
      -3  no image URL at all"""
    ethn_l = ethnicity.lower()
    title = _get_text(item, "title", "dcTitleLangAware")
    desc = _get_text(item, "dcDescription", "dcSubject", "dcCreator", "dcType")
    title_l = title.lower()
    desc_l = desc.lower()
    score = 0.0
    if ethn_l in title_l:
        score += 5
    elif ethn_l in desc_l:
        score += 2
    for t in (seed_traditions or []):
        t_l = t.lower()
        if len(t_l) >= 4:  # avoid super-generic matches
            if t_l in title_l:
                score += 3
            elif t_l in desc_l:
                score += 1
    provider_l = (_first(item.get("dataProvider")) or "").lower()
    if any(tok in provider_l for tok in ("wereldculturen", "quai branly", "kit ", "tropenmuseum",
                                          "ethnographic", "world culture", "national museum",
                                          "asian civilisations", "musée")):
        score += 1
    # Noise penalties
    for junk in ("specimen", "herbarium", "holotype", "fossil"):
        if junk in title_l or junk in desc_l:
            score -= 10
    if not _first(item.get("edmPreview")):
        score -= 3
    return score


def _get_key() -> str:
    if "EUROPEANA_API_KEY" in os.environ:
        return os.environ["EUROPEANA_API_KEY"]
    vault_path = Path(__file__).resolve().parents[4] / "tools" / "vault" / "vault.toml"
    if vault_path.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore
        data = tomllib.loads(vault_path.read_text(encoding="utf-8"))
        key = ((data.get("apis") or {}).get("europeana") or {}).get("key")
        if key:
            return key
    raise RuntimeError("No Europeana key. Set EUROPEANA_API_KEY or add to vault.")


def search(client: RateLimitedClient, query: str, rows: int = 40, cursor: str = "*") -> dict:
    """One page of Europeana search. Uses cursor pagination (recommended for deep results)."""
    key = _get_key()
    params = {
        "wskey": key,
        "query": query,
        "rows": rows,
        "cursor": cursor,
        "media": "true",
        "reusability": "open,permission",
        "profile": "standard",
    }
    return client.get_json(f"{BASE}/search.json", params=params)


def _extract_place(item: dict) -> str | None:
    """Combine city + country when present."""
    parts: list[str] = []
    place = _first(item.get("dcCreator")) or _first(item.get("edmPlaceLabel"))
    if isinstance(place, str):
        parts.append(place)
    country = _first(item.get("country"))
    if isinstance(country, str):
        parts.append(country)
    return " · ".join(parts) if parts else None


def _to_canonical(item: dict, cultural: dict) -> dict:
    """Flatten a Europeana search item into our canonical record shape.

    Uses the light schema (title, preview URL, provider, guid) — Europeana's
    Record API gives fuller data via a second call, but the search response
    already has enough for a browsable gallery tile."""
    from ..schema import _empty_record, MUSEUM_NAMES  # local import to keep cycles clean

    guid = item.get("id") or item.get("guid") or "?"
    oid = guid.lstrip("/").replace("/", "_")
    r = _empty_record("europeana", oid)
    r["cultural"].update(cultural)

    title = _first(item.get("title")) or _first(item.get("dcTitleLangAware", {}).get("def") if isinstance(item.get("dcTitleLangAware"), dict) else None)
    r["physical"]["title"] = title
    r["physical"]["date_text"] = _first(item.get("year")) or _first(item.get("edmTimespanLabel"))
    r["physical"]["classification"] = _first(item.get("edmType"))

    provider = _first(item.get("dataProvider")) or _first(item.get("provider"))
    r["source"]["museum_name"] = provider or MUSEUM_NAMES["europeana"]
    r["source"]["object_url"] = item.get("guid") or (
        f"https://www.europeana.eu/en/item{item.get('id')}" if item.get("id") else None
    )
    rights = _first(item.get("rights"))
    r["source"]["rights"] = "public-domain" if (rights and "publicdomain" in rights.lower()) else (rights or "unknown")
    r["source"]["license_url"] = rights

    r["location"]["made_in_place"] = _extract_place(item)
    r["location"]["current_museum"] = provider

    # Preview image — prefer edmIsShownBy (the museum's full-resolution URL,
    # typically 800-3000px wide) over edmPreview (Europeana's ~200px cached
    # thumbnail). Store both so the downloader can fall back to edmPreview
    # if the shown-by URL returns 500/403 (Wereldculturen's imageproxy).
    thumb = _first(item.get("edmPreview"))
    full = _first(item.get("edmIsShownBy"))
    chosen = full or thumb
    if chosen:
        img = {
            "url": chosen,
            "iiif_id": None, "iiif_base": None,
            "role": "primary", "sha256": None, "bytes": None, "local_path": None,
        }
        if full and thumb and full != thumb:
            img["fallback_url"] = thumb
        r["images"].append(img)
    r["raw"] = item
    return r


def scrape_ethnicity(
    client: RateLimitedClient,
    region: str,
    country: str,
    ethnicity: str,
    queries: list[str],
    seed_traditions: list[str] | None = None,
    max_per_query: int = 50,
    max_total: int = 40,
    min_score: float = 3.0,
) -> int:
    """Search Europeana for each query, score by relevance, save the top matches.

    Filter pipeline:
      1. Reject non-cultural providers (natural history, botanical, etc.)
      2. Score each item by title/desc/provider relevance to the ethnicity
      3. Keep only items with score >= min_score
      4. Download preview image; drop records whose image download fails.
    """
    from ..schema import _empty_record  # noqa

    cache_key = f"europeana__{country.replace(' ','_')}__{ethnicity.replace(' ','_')}"
    cache = raw_path("europeana", cache_key)
    if cache.exists():
        raw_items = json.loads(cache.read_text(encoding="utf-8"))
    else:
        raw_items = []
        seen_ids: set[str] = set()
        for q in queries:
            try:
                j = search(client, q, rows=max_per_query)
            except Exception as e:
                print(f"  ! europeana search {q!r} failed: {e}", flush=True)
                continue
            for it in j.get("items", []):
                gid = it.get("id")
                if not gid or gid in seen_ids:
                    continue
                provider = _first(it.get("dataProvider")) or _first(it.get("provider"))
                if not _is_cultural_provider(provider):
                    log_reject(source="europeana", reason="non-cultural-provider",
                               region=region, country=country, ethnicity=ethnicity,
                               title=_first(it.get("title")),
                               extra={"provider": provider})
                    continue
                # Drop non-visual records (books, sound, video). Historic gap:
                # `type` field is a plain string ("SOUND"), not a list; the
                # old `_first` returned None for scalars, so SOUND records
                # sneaked through as edm_type=None. `_first` now returns
                # scalar strings as-is; belt-and-suspenders check below.
                edm_type = _first(it.get("edmType")) or _first(it.get("type"))
                if edm_type and str(edm_type).upper() not in ("IMAGE", "3D"):
                    log_reject(source="europeana", reason=f"non-image-{edm_type}",
                               region=region, country=country, ethnicity=ethnicity,
                               title=_first(it.get("title")))
                    continue
                # Belt-and-suspenders: reject any record whose `edmIsShownBy`
                # (full-res URL) points at a known-audio provider even if
                # edmType is missing. Same for map records — reject when the
                # `dcType` field says map.
                _shown = (_first(it.get("edmIsShownBy")) or "").lower()
                if (
                    "crem-cnrs" in _shown or "crem.cnrs" in _shown
                    or "/sounds/" in _shown or "timeside" in _shown
                    or "/archives_items/" in _shown or "/archives/items/" in _shown
                ):
                    log_reject(source="europeana", reason="audio-archive-passthrough",
                               region=region, country=country, ethnicity=ethnicity,
                               title=_first(it.get("title")))
                    continue
                # Estonian/Baltic public-sculpture misroute (Haapsalu, Raudsepp).
                # Rare but keeps popping up because Europeana's Estonia data
                # provider indexes Estonian sculpture titles with tokens that
                # rank higher than actual target-country records.
                _all_text = " ".join([
                    _first(it.get("title")) or "",
                    _first(it.get("dcDescription")) or "",
                    _first(it.get("dcCreator")) or "",
                ])
                if re.search(
                    r"\b(Haapsalu|Tartu|Tallinn|Juhan Raudsepp|Poiss kalaga)\b",
                    _all_text, re.I,
                ):
                    log_reject(source="europeana", reason="estonian-misroute",
                               region=region, country=country, ethnicity=ethnicity,
                               title=_first(it.get("title")))
                    continue
                dc_type = _first(it.get("dcType")) or ""
                _title = _first(it.get("title")) or ""
                if (
                    "map" in dc_type.lower()
                    or re.match(r"^(Carte|Mapa|Carta|Map|Kaart)\b", _title, re.I)
                ):
                    log_reject(source="europeana", reason="map-not-artifact",
                               region=region, country=country, ethnicity=ethnicity,
                               title=_title)
                    continue
                # Ingest-time junk gate.
                from ..junk import should_reject
                title = _first(it.get("title")) or ""
                desc = _first(it.get("dcDescription")) or ""
                is_junk, junk_reason = should_reject(title, desc, provider or "")
                if is_junk:
                    log_reject(source="europeana", reason=f"junk-{junk_reason}",
                               region=region, country=country, ethnicity=ethnicity,
                               title=title, extra={"provider": provider})
                    continue
                # Hard geo gate.
                if not _record_matches_target_country(it, region, country):
                    log_reject(source="europeana", reason="no-geo-evidence",
                               region=region, country=country, ethnicity=ethnicity,
                               title=title, extra={"provider": provider})
                    continue
                # Anti-topic gate for ambiguous ethnonyms (San, Chin, etc.)
                # Blocks Italian saint-name architecture from a "San" search.
                if _is_ambiguous_false_positive(it, ethnicity):
                    log_reject(source="europeana", reason="ambiguous-ethnonym-false-positive",
                               region=region, country=country, ethnicity=ethnicity,
                               title=title, extra={"provider": provider})
                    continue
                # Provider-collision gate (Bamar "kalaga" ~ Estonian comitative
                # case; Estonian museums surface fish still-lifes as Bamar
                # matches). See _HOSTILE_PROVIDER_BY_ETHNICITY.
                if _is_provider_collision(it, ethnicity):
                    log_reject(source="europeana", reason="hostile-provider-collision",
                               region=region, country=country, ethnicity=ethnicity,
                               title=title, extra={"provider": provider})
                    continue
                # Museum-catalog entries, cross-culture (Surinamese) mis-file,
                # commercial ephemera (candy wrappers). Title-only match — the
                # patterns are anchored so this is safe.
                if _is_generic_nonartifact(it):
                    log_reject(source="europeana", reason="generic-nonartifact-title",
                               region=region, country=country, ethnicity=ethnicity,
                               title=title, extra={"provider": provider})
                    continue
                seen_ids.add(gid)
                raw_items.append(it)
        cache.write_text(json.dumps(raw_items, ensure_ascii=False), encoding="utf-8")

    if not raw_items:
        return 0

    # Score + rank items by relevance to this ethnicity.
    scored = [(_score_relevance(it, ethnicity, seed_traditions), it) for it in raw_items]
    scored = [(s, it) for s, it in scored if s >= min_score]
    scored.sort(key=lambda x: -x[0])
    items = [it for _, it in scored[:max_total]]
    if not items:
        return 0

    saved = 0
    for item in items:
        title = _first(item.get("title")) or ""
        cf = classify({"classification": _first(item.get("edmType")) or "", "object_type": "", "title": title})
        cultural = {
            "region": region, "country": country, "ethnicity": ethnicity,
            "tradition": queries[0] if queries else ethnicity,
            "art_form": cf["art_form"], "pattern_density": cf["pattern_density"],
        }
        rec = _to_canonical(item, cultural)
        dest = library_path(region, country, ethnicity, cf["art_form"], (queries[0] if queries else ethnicity))
        for i, img in enumerate(rec["images"]):
            filename = f"eu_{rec['id'].split('-',1)[-1]}_{i}.jpg" if i else f"eu_{rec['id'].split('-',1)[-1]}.jpg"
            dst = dest / "images" / filename
            try:
                sha, size = download_image(client, img["url"], dst)
                img["sha256"] = sha
                img["bytes"] = size
                img["local_path"] = str(dst.relative_to(dest.parents[5]))
            except Exception as e:
                print(f"  ! europeana {rec['id']} img {i} download failed: {e}", flush=True)
        rec["images"] = [i for i in rec["images"] if i.get("local_path")]
        if not rec["images"]:
            continue
        append_metadata(dest, rec)
        saved += 1
    return saved
