"""Fetch open-source media + reference material for one ethnicity.

Four sources, all free / no-paid-API:

- **Wikipedia (MediaWiki)** — the article body, sectioned. Used as grounding
  context for the writeup generator so Claude synthesizes from a real source
  instead of relying on model memory alone.
- **Wikimedia Commons** — CC-BY / public-domain photographs grouped under
  Category pages. We resolve a short list of candidate categories (Culture of X,
  X people, X dance, X cuisine) and pull thumbnails.
- **UNESCO ICH** — the inscription list. The UNESCO site itself is bot-blocked
  (Akamai BobCMN), so we query Wikidata via SPARQL for items with UNESCO ICH
  identifier (P10221) filtered by country (P495).
- **Smithsonian Folkways** — searched via the Smithsonian Open Access API
  (`api.si.edu`) with the api.data.gov key. No embedded audio; link-out only.

The output is a single JSON sidecar per ethnicity, consumed by both the
grounded writeup generator and the index builder.
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

import requests

UA = "folk-patterns/0.1 (research atlas; https://github.com/abobabo91)"
TIMEOUT = 30

# Minimum interval between requests to any single host, to keep us politely
# under Wikimedia's soft-rate-limit for anonymous API clients. Batch runs
# without this were seeing sporadic empty responses (Uzbek getting commons=0,
# ich=0) despite the same requests working immediately in single-shot mode.
_LAST_REQ_TS: dict[str, float] = {}


def _polite_wait(host: str, min_interval: float = 0.8) -> None:
    import time
    last = _LAST_REQ_TS.get(host, 0.0)
    wait = min_interval - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    _LAST_REQ_TS[host] = time.time()


def _get(url: str, params: dict | None = None, headers: dict | None = None,
         min_interval: float = 0.8, retries: int = 3, **_ignored) -> requests.Response:
    """Wrapper around requests.get with per-host rate limiting and retries.
    Accepts (and ignores) `timeout=` so call sites migrating from bare
    `requests.get(...)` don't need to change signatures. Any 429/5xx retried
    with exponential backoff; raises on final failure."""
    import time
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    for attempt in range(retries):
        _polite_wait(host, min_interval)
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=TIMEOUT)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise requests.exceptions.RequestException(f"exhausted retries for {url}")

# --- Wikidata Q-IDs for the countries we currently index. Extend as new
# regions come online. Keeping this centrally means every downstream consumer
# (Wikipedia search fallback, UNESCO ICH filter, Folkways country hint) shares
# the same normalization.
COUNTRY_QID: dict[str, str] = {
    # central-asia
    "Uzbekistan": "Q265",
    "Kazakhstan": "Q232",
    "Kyrgyzstan": "Q813",
    "Turkmenistan": "Q874",
    "Tajikistan": "Q863",
    "Afghanistan": "Q889",
    "China (Xinjiang)": "Q148",
    # southeast-asia
    "Indonesia": "Q252",
    "Malaysia": "Q833",
    "Thailand": "Q869",
    "Vietnam": "Q881",
    "Cambodia": "Q424",
    "Laos": "Q819",
    "Myanmar": "Q836",
    "Philippines": "Q928",
}


# =========================================================================
#  Wikipedia
# =========================================================================

def _strip_parenthetical(name: str) -> str:
    """'Uzbek (Afghanistan)' -> 'Uzbek'. Preserves the base name for lookup."""
    return re.sub(r"\s*\([^)]*\)\s*", " ", name).strip()


def _normalize_for_match(s: str) -> str:
    """Lowercase + strip common punctuation/whitespace variants so 'T'boli'
    matches 'Tboli' and 'Lao-Isan' matches 'Lao Isan'."""
    s = s.lower()
    # Drop apostrophes (T'boli vs Tboli), hyphens and slashes, then collapse whitespace.
    for ch in ("'", "’", "-", "/", "–", "—"):
        s = s.replace(ch, "")
    return " ".join(s.split())


def _title_matches_ethnicity(title: str, ethnicity: str) -> bool:
    """Guard against Wikipedia search returning off-topic articles.

    The resolved article title must contain the ethnicity's base name — or,
    for compound ethnicity names (Lao Isan, Kazakh-Xinjiang), any significant
    token from the base name. Case- and punctuation-insensitive.

    Examples:
      "Uzbek people" matches "Uzbek" ✓
      "Tboli people" matches "T'boli" ✓ (apostrophe-insensitive)
      "Isan people" matches "Lao Isan" ✓ (token match)
      "Six plus Two Group on Afghanistan" does NOT match "Uzbek" ✗"""
    base = _normalize_for_match(_strip_parenthetical(ethnicity))
    t = _normalize_for_match(title)
    if base in t:
        return True
    base_stem = base.rstrip("s")
    if base_stem and base_stem in t:
        return True
    # Compound-name fallback: accept if any 3+ char token from base is present.
    tokens = [tok for tok in base.split() if len(tok) >= 3]
    for tok in tokens:
        if tok in t or tok.rstrip("s") in t:
            return True
    return False


# Explicit override for ethnicity names that don't match any autoderivable
# Wikipedia title. Keep this small — only cases the resolver can't figure out.
WIKI_TITLE_OVERRIDES: dict[str, str] = {
    "Kinh": "Vietnamese people",             # Kinh is the endonym; Wikipedia uses "Vietnamese people"
    "Lao Isan": "Isan people",               # More specific than the token-fallback "Lao people"
}


def wiki_resolve_title(ethnicity: str, country: str) -> str | None:
    """Best-guess canonical Wikipedia title for an ethnic group.

    Strategy:
      1. Strip any parenthetical qualifier from the ethnicity name
         ("Uzbek (Afghanistan)" -> "Uzbek") before probing, since Wikipedia
         doesn't use the "(Country)" convention for ethnic-group articles.
      2. Try direct lookups: "<Base> people", "<Base>s", "<Base>".
      3. Fall back to full-text search — but VALIDATE that the returned
         article title still contains the ethnicity's base name. Without this
         guard, ambiguous searches ("Uzbek Afghanistan ethnic group") return
         unrelated political / geographic articles ("Six plus Two Group on
         Afghanistan").
      4. Return None if nothing matches — better a blank grounding than
         grounding on the wrong article."""
    # Manual override wins over everything.
    if ethnicity in WIKI_TITLE_OVERRIDES:
        return WIKI_TITLE_OVERRIDES[ethnicity]

    base = _strip_parenthetical(ethnicity)
    candidates: list[str] = [f"{base} people", f"{base}s", base]
    # For compound / hyphenated names ("Lao Isan", "Kazakh-Xinjiang") also
    # try each significant token individually — often the article title uses
    # just one of them (Wikipedia has "Isan people" but not "Lao Isan people").
    # We try the LAST token first because it's typically the more specific
    # ethnonym ("Lao Isan" → "Isan people" better than "Lao people").
    tokens = [t for t in re.split(r"[\s\-_]", base) if len(t) > 2]
    if len(tokens) > 1:
        for t in reversed(tokens):
            candidates.extend([f"{t} people", f"{t}s", t])
    for cand in candidates:
        r = _get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "titles": cand, "redirects": 1, "format": "json"},
            headers={"User-Agent": UA}, timeout=TIMEOUT,
        )
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for _, p in pages.items():
            if "missing" not in p and p.get("title") and _title_matches_ethnicity(p["title"], ethnicity):
                return p["title"]
    # Fallback: full-text search restricted to article space, but only accept a
    # hit whose title still names the ethnicity.
    r = _get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "list": "search",
            "srsearch": f'"{base}" ethnic group {country}',
            "srlimit": 5, "srnamespace": 0, "format": "json",
        },
    )

    for hit in r.json().get("query", {}).get("search", []):
        title = hit.get("title", "")
        if _title_matches_ethnicity(title, ethnicity):
            return title
    return None


def wiki_fetch_article(title: str, max_chars: int = 25_000) -> dict:
    """Fetch the plain-text article body + section outline for a title.

    Returns { title, url, intro, sections: [{level, line, text}], full_text }.
    Full text is truncated to `max_chars` to keep prompts affordable."""
    # Intro (single call).
    r = _get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "prop": "extracts", "exintro": 1,
            "explaintext": 1, "redirects": 1, "titles": title, "format": "json",
        },
    )

    pages = r.json().get("query", {}).get("pages", {})
    p = next(iter(pages.values()))
    intro = p.get("extract", "") if p else ""

    # Full plaintext (single call; we want everything, not just intro).
    r = _get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "prop": "extracts", "explaintext": 1,
            "redirects": 1, "titles": title, "format": "json",
        },
    )

    pages = r.json().get("query", {}).get("pages", {})
    p = next(iter(pages.values()))
    full_text = (p.get("extract", "") if p else "")[:max_chars]

    # Section outline.
    r = _get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "parse", "page": title, "prop": "sections", "format": "json"},
    )

    sects = r.json().get("parse", {}).get("sections", []) or []
    sections = [{"index": s.get("index"), "level": int(s.get("level", 2)), "line": s.get("line", "")} for s in sects]

    return {
        "title": title,
        "url": f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
        "intro": intro,
        "sections": sections,
        "full_text": full_text,
    }


# =========================================================================
#  Wikimedia Commons
# =========================================================================

def commons_category_exists(cat: str) -> bool:
    r = _get(
        "https://commons.wikimedia.org/w/api.php",
        params={"action": "query", "titles": f"Category:{cat}", "format": "json"},
    )

    pages = r.json().get("query", {}).get("pages", {})
    p = next(iter(pages.values()))
    return "missing" not in p


# Titles that reliably indicate a bad candidate for a cultural gallery.
# Matched case-insensitively as substrings on the file title.
_BAD_TITLE_TOKENS = (
    "coat of arms", "flag of", "map of", "seal of", "emblem of",
    "logo", "signature", "election", "protest", "war crime",
    "atrocit", "beaten", "bayonet", "coffin", "casualt",
    "diagram", "graph of", "chart of", "table of",
    "toy ", " toy.", " toy_", " cake", " pizza", " bambi",
    "meme", "graffiti", "screenshot",
)


def _bad_title(title: str) -> bool:
    lo = title.lower()
    if any(tok in lo for tok in _BAD_TITLE_TOKENS):
        return True
    # Amateur snapshot heuristic: title starts with an English indefinite
    # article ("A old man toy", "An unusual view of..."). Culture-worthy files
    # usually have a proper subject noun first (e.g. "Suzani, Bukhara, 19c").
    stem = lo.split(".")[0]
    tokens = stem.split()
    if len(tokens) >= 2 and tokens[0] in ("a", "an") and not tokens[1][0].isupper():
        return True
    return False

# Preferred license short-names. Anything else gets kept but ranked lower.
_GOOD_LICENSES = ("cc0", "public domain", "cc-by", "cc by")


def _license_ok(license_str: str) -> bool:
    if not license_str:
        return True   # unknown license: don't drop, just deprioritize
    lo = license_str.lower()
    if any(g in lo for g in _GOOD_LICENSES):
        return True
    # Reject explicit fair-use / non-free tags.
    if "fair use" in lo or "non-free" in lo or "all rights reserved" in lo:
        return False
    return True


def _quality_tier(meta: dict) -> int:
    """Commons has explicit quality flags in extmetadata.
    Featured (3) > Quality image (2) > Valued image (1) > none (0)."""
    for k in ("AssessmentsShortName", "Assessments"):
        v = meta.get(k) or {}
        if isinstance(v, dict):
            val = str(v.get("value", "")).lower()
            if "featured" in val:
                return 3
            if "quality" in val:
                return 2
            if "valued" in val:
                return 1
    return 0


def _score_photo(rec: dict, meta: dict) -> float:
    """Compose a rank score for one candidate photo.

    Higher is better. Combines quality tier, resolution, and metadata
    completeness. Category priority is applied later at the merge step."""
    if not _license_ok(rec.get("license", "")):
        return -1  # forces drop
    if _bad_title(rec["title"]):
        return -1
    w, h = rec.get("width") or 0, rec.get("height") or 0
    if w < 800 or h < 600:
        return -1  # too small for a gallery tile
    score = 0.0
    score += _quality_tier(meta) * 10       # dominant signal
    score += min(w * h, 4_000_000) / 500_000   # bigger photos, up to a cap
    if rec.get("description"):
        score += 0.5
    if rec.get("credit"):
        score += 0.3
    return score


def commons_fetch_photos(category: str, limit: int = 12) -> list[dict]:
    """Return the top `limit` scored file records from one Commons category."""
    r = _get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query", "generator": "categorymembers",
            "gcmtitle": f"Category:{category}", "gcmtype": "file",
            "gcmlimit": 200,  # over-fetch aggressively so we can rank + drop
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|size|mime",
            "iiurlwidth": 800,
            "format": "json",
        },
    )

    pages = r.json().get("query", {}).get("pages", {})
    scored: list[tuple[float, dict]] = []
    for _, p in pages.items():
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii:
            continue
        mime = ii.get("mime", "")
        if not mime.startswith("image/") or mime == "image/svg+xml":
            continue
        meta = ii.get("extmetadata", {}) or {}
        def _m(k: str) -> str:
            v = meta.get(k) or {}
            return _strip_html(v.get("value", "")) if isinstance(v, dict) else ""
        rec = {
            "title": p.get("title", "").replace("File:", ""),
            "thumb_url": ii.get("thumburl") or ii.get("url"),
            "full_url": ii.get("url"),
            "page_url": f"https://commons.wikimedia.org/wiki/{quote(p.get('title',''))}",
            "credit": _m("Artist") or _m("Credit"),
            "license": _m("LicenseShortName") or _m("UsageTerms"),
            "description": _m("ImageDescription"),
            "width": ii.get("width"),
            "height": ii.get("height"),
            "quality_tier": _quality_tier(meta),
        }
        s = _score_photo(rec, meta)
        if s < 0:
            continue
        scored.append((s, rec))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:limit]]


def _strip_html(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"<[^>]+>", "", s).strip()


def commons_from_wiki_article(wiki_title: str, limit: int = 12) -> list[dict]:
    """Pull images embedded in a Wikipedia article. These are hand-picked by
    editors and near-always relevant and well-composed — much better default
    than random Category:X photos.

    Uses `action=parse&page=X&prop=images` to get filenames, then a single
    `imageinfo` batch call to enrich each with URLs + license + size."""
    r = _get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "parse", "page": wiki_title, "prop": "images", "format": "json"},
    )

    fnames = r.json().get("parse", {}).get("images", []) or []
    # Skip icons / non-photos by extension.
    fnames = [f for f in fnames if f.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"))][:40]
    if not fnames:
        return []
    titles = "|".join(f"File:{f}" for f in fnames)
    r = _get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query", "titles": titles, "prop": "imageinfo",
            "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 800,
            "format": "json",
        },
    )

    pages = r.json().get("query", {}).get("pages", {})
    scored: list[tuple[float, dict]] = []
    # Preserve article ordering as a soft tiebreaker (earlier images tend to
    # be the lede / more representative).
    for order, (_, p) in enumerate(pages.items()):
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii:
            continue
        mime = ii.get("mime", "")
        if not mime.startswith("image/") or mime == "image/svg+xml":
            continue
        meta = ii.get("extmetadata", {}) or {}
        def _m(k: str) -> str:
            v = meta.get(k) or {}
            return _strip_html(v.get("value", "")) if isinstance(v, dict) else ""
        rec = {
            "title": p.get("title", "").replace("File:", ""),
            "thumb_url": ii.get("thumburl") or ii.get("url"),
            "full_url": ii.get("url"),
            "page_url": f"https://commons.wikimedia.org/wiki/{quote(p.get('title',''))}",
            "credit": _m("Artist") or _m("Credit"),
            "license": _m("LicenseShortName") or _m("UsageTerms"),
            "description": _m("ImageDescription"),
            "width": ii.get("width"),
            "height": ii.get("height"),
            "quality_tier": _quality_tier(meta),
            "source_category": "(Wikipedia article)",
        }
        s = _score_photo(rec, meta)
        if s < 0:
            continue
        # Big bonus (+20) for being article-embedded (beats even featured
        # category photos), plus a small penalty for later ordering.
        scored.append((s + 20 - order * 0.05, rec))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:limit]]


def commons_gather(ethnicity: str, country: str, extra_categories: list[str] | None = None,
                   wiki_title: str | None = None,
                   per_cat_limit: int = 10, total_limit: int = 12) -> list[dict]:
    """Try several Commons categories, score every candidate, return the top N.

    Category priority is applied as a bonus on top of the per-photo quality
    score. Most-specific/most-cultural categories score highest.

    Priority order (with per-category bonuses):
      1. `<Ethnicity> culture` / `Culture of <Country>` (+5) — usually the
         richest source of festival / dance / craft photos.
      2. `<Ethnicity>` / `<Ethnicity>s` (+3) — mixed; people + culture.
      3. seed traditions like 'Suzani', 'Longyi' (+4) — very targeted.
      4. `<Ethnicity> people` (+1) — often just headshots, deprioritize.
    """
    # (category, category-bonus)
    candidates: list[tuple[str, float]] = [
        (f"{ethnicity} culture", 5.0),
        (f"Culture of {country}", 5.0),
        (ethnicity, 3.0),
        (f"{ethnicity}s", 3.0),
        (f"{ethnicity} people", 1.0),
    ]
    for t in (extra_categories or []):
        candidates.append((t, 4.0))

    all_scored: list[tuple[float, dict]] = []
    seen_titles: set[str] = set()

    # Primary source: images embedded in the Wikipedia article. Curated by
    # Wikipedia editors, so far better default than raw category dumps.
    if wiki_title:
        for p in commons_from_wiki_article(wiki_title, limit=total_limit):
            if p["title"] in seen_titles:
                continue
            seen_titles.add(p["title"])
            all_scored.append((100 + p.get("quality_tier", 0) * 10, p))

    for cat, bonus in candidates:
        if not commons_category_exists(cat):
            continue
        photos = commons_fetch_photos(cat, limit=per_cat_limit)
        for p in photos:
            if p["title"] in seen_titles:
                continue
            seen_titles.add(p["title"])
            p["source_category"] = cat
            # Reconstruct a rank score = original quality (baseline 0) plus
            # category priority bonus. Photos with a Commons quality tier
            # already got 10-30 points; category bonus is tie-breaker.
            base = p.get("quality_tier", 0) * 10 + 1.0
            all_scored.append((base + bonus, p))
        time.sleep(0.15)  # be polite

    all_scored.sort(key=lambda x: -x[0])
    return [p for _, p in all_scored[:total_limit]]


# =========================================================================
#  UNESCO ICH via Wikidata SPARQL
# =========================================================================

SPARQL_URL = "https://query.wikidata.org/sparql"

# Extra tokens for scoring ICH-inscription relevance to a specific ethnicity.
# Beyond the ethnicity name itself, these regional / linguistic / religious
# words strongly indicate an inscription belongs to that group even when the
# Wikidata label doesn't literally name the ethnicity ("Meshrep" is Uyghur,
# "Manas epic" is Kyrgyz, etc.).
ETHNICITY_ICH_HINTS: dict[str, list[str]] = {
    "Uyghur": ["uyghur", "xinjiang", "muqam", "meshrep", "dolan", "turkic"],
    "Kazakh (Xinjiang)": ["kazakh", "xinjiang", "dombra", "kuresi", "aitys"],
    "Kazakh": ["kazakh", "dombra", "kuresi", "aitys", "yurt"],
    "Kyrgyz": ["kyrgyz", "manas", "yurt", "ak kalpak", "komuz"],
    "Turkmen": ["turkmen", "carpet weaving", "akhal"],
    "Uzbek": ["uzbek", "bukhara", "samarkand", "khiva", "shashmaqam", "askiya",
              "katta ashula", "lazgi", "bakshy", "boysun"],
    "Tajik": ["tajik", "falak", "chakan", "atlas", "adras"],
    "Pamiri": ["pamir", "chakan"],
    "Karakalpak": ["karakalpak"],
    "Bukharan Jew": ["bukhara", "shashmaqam"],
    "Uzbek (Afghanistan)": ["uzbek", "afghan"],
    "Afghan Turkmen": ["turkmen", "afghan"],
    "Hazara": ["hazara"],
    # SE Asia — Indonesia has 14 shared entries; each ethnicity gets its own.
    "Javanese": ["javanese", "batik", "wayang", "gamelan", "kris", "borobudur"],
    "Balinese": ["bali", "gamelan"],
    "Sundanese": ["sunda", "angklung"],
    "Batak": ["batak"],
    "Minangkabau": ["minangkabau", "randai", "rumah gadang"],
    "Dayak": ["dayak", "iban", "borneo"],
    "Toraja": ["toraja"],
    "Malay": ["malay", "silat", "mak yong", "dondang sayang", "wau"],
    "Iban": ["iban", "borneo", "dayak", "pua kumbu"],
    "Thai": ["thai", "khon", "nora", "songkran", "nuad"],
    "Lao Isan": ["isan", "lao", "khaen", "mor lam"],
    "Kinh": ["vietnam", "vietnamese", "quan ho", "ca tru", "hue"],
    "Hmong": ["hmong"],
    "Cham": ["cham"],
    "Khmer": ["khmer", "cambodia", "sbek thom", "chapei", "kun lbokator", "lkhon"],
    "Lao": ["lao", "khaen"],
    "Bamar": ["bamar", "burma", "myanmar"],
    "Chin": ["chin", "myanmar"],
    "Filipino": ["philippine", "filipino", "hudhud", "punnuk", "buklog"],
    "T'boli": ["tboli", "t'nalak", "mindanao", "lake sebu"],
    "Yakan": ["yakan"],
}


def _score_ich_relevance(entry: dict, ethnicity: str) -> float:
    """Score how likely an ICH inscription belongs to this specific ethnicity.
    Higher = more relevant. Combines exact ethnicity-name match + curated hint
    tokens for the group's language, region, and named traditions."""
    hay = " ".join(filter(None, [
        entry.get("title", ""), entry.get("description", ""),
        entry.get("commons_category", ""),
    ])).lower()
    if not hay:
        return 0.0
    score = 0.0
    ethn_l = _strip_parenthetical(ethnicity).lower()
    if ethn_l in hay:
        score += 5.0
    for hint in ETHNICITY_ICH_HINTS.get(ethnicity, []):
        if hint.lower() in hay:
            score += 2.0
    return score


# Majority (or dominant / titular) ethnicity per country. These groups inherit
# the country's UNESCO ICH list wholesale — the "national heritage" of that
# state is by definition attributable to them. Minority ethnicities only see
# ICH items that specifically name them (via ETHNICITY_ICH_HINTS).
COUNTRY_MAJORITY_ETHNICITY: dict[str, str] = {
    "Uzbekistan": "Uzbek",
    "Kazakhstan": "Kazakh",
    "Kyrgyzstan": "Kyrgyz",
    "Turkmenistan": "Turkmen",
    "Tajikistan": "Tajik",
    "Afghanistan": None,        # multi-ethnic, no single majority — leave all minorities on hints only
    "China (Xinjiang)": None,   # ditto
    "Indonesia": "Javanese",    # Javanese ~40% + heavily dominant in national symbols
    "Malaysia": "Malay",
    "Thailand": "Thai",
    "Vietnam": "Kinh",
    "Cambodia": "Khmer",
    "Laos": "Lao",
    "Myanmar": "Bamar",
    "Philippines": "Filipino",
}


def _filter_ich_by_relevance(entries: list[dict], ethnicity: str, country: str = "") -> list[dict]:
    """Reduce a country's UNESCO ICH list to entries relevant to this ethnicity.

    Logic:
      - Very small lists (≤2 entries): keep all — not enough to clutter.
      - The country's MAJORITY / titular ethnicity inherits the full list —
        national heritage is theirs by default (Uzbek gets all 11 Uzbekistan
        inscriptions including Nowruz + pilaf; Bamar gets Myanmar's).
      - MINORITY ethnicities only see items that name them or a strongly
        associated hint (Karakalpak keeps only Karakalpak-specific entries).
      - If no minority match, return empty rather than drown the sidebar in
        national items that don't belong to the group."""
    if len(entries) <= 2:
        return entries
    if COUNTRY_MAJORITY_ETHNICITY.get(country) == ethnicity:
        return entries[:15]   # majority: keep all up to cap
    # Minority: score by ethnicity-specific relevance hints only.
    scored = sorted(
        ((_score_ich_relevance(e, ethnicity), e) for e in entries),
        key=lambda x: -x[0],
    )
    kept = [e for s, e in scored if s > 0]
    return kept[:12]


def unesco_ich_for_country(country: str) -> list[dict]:
    """UNESCO Intangible Cultural Heritage inscriptions with country = X.

    Uses Wikidata P10221 (UNESCO ICH identifier) filtered by P495 (country of
    origin). Each result carries the ICH code (RL/00089, USL/00123, GS/...)
    and, when available, a Commons category for supporting imagery."""
    qid = COUNTRY_QID.get(country)
    if not qid:
        return []
    query = f"""
        SELECT ?item ?itemLabel ?ichCode ?commonsCat ?description WHERE {{
          ?item wdt:P10221 ?ichCode .
          ?item wdt:P495 wd:{qid} .
          OPTIONAL {{ ?item wdt:P373 ?commonsCat }} .
          OPTIONAL {{ ?item schema:description ?description . FILTER(LANG(?description) = 'en') }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language 'en' }}
        }}
        LIMIT 50
    """
    r = _get(SPARQL_URL, params={"query": query, "format": "json"},
                     headers={"User-Agent": UA, "Accept": "application/sparql-results+json"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    out: list[dict] = []
    for b in r.json().get("results", {}).get("bindings", []):
        code = b.get("ichCode", {}).get("value")
        label = b.get("itemLabel", {}).get("value")
        cc = b.get("commonsCat", {}).get("value")
        desc = b.get("description", {}).get("value")
        if not code or not label:
            continue
        out.append({
            "code": code,                                                     # RL/00089
            "title": label,                                                   # shashmaqam
            "description": desc,                                              # short one-liner
            "unesco_url": f"https://ich.unesco.org/en/{code}",                # canonical link
            "commons_category": cc,                                           # for optional thumbnail
            "wikidata_url": b.get("item", {}).get("value"),
        })
    # Deduplicate — some inscriptions appear twice due to multiple country_of_origin values.
    seen: set[str] = set()
    dedup: list[dict] = []
    for e in out:
        if e["code"] in seen:
            continue
        seen.add(e["code"])
        dedup.append(e)
    return dedup




# =========================================================================
#  Smithsonian Folkways (via Smithsonian Open Access API)
# =========================================================================

SI_API = "https://api.si.edu/openaccess/api/v1.0/search"

# Country aliases: canonical name -> extra names the Smithsonian catalog is
# likely to use. Kept short and curated — only cases where the catalog
# systematically uses a different form than the current political name.
# Most Central Asian records were catalogued post-independence and use the
# current name, so no alias needed there.
COUNTRY_ALIASES: dict[str, list[str]] = {
    "Myanmar": ["Burma"],
    "Thailand": ["Siam"],
    "Vietnam": ["Vietnamese"],
    "Cambodia": ["Khmer"],
    "Laos": ["Laotian"],
    "Philippines": ["Filipino"],
    "Indonesia": ["Indonesian"],
    "Malaysia": ["Malaysian"],
    "China (Xinjiang)": ["Xinjiang", "Chinese Turkestan"],
}


def _si_extract_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows or []:
        title = row.get("title") or (
            (row.get("content", {}).get("descriptiveNonRepeating", {})
                .get("title", {}) or {}).get("content")
        )
        dnr = row.get("content", {}).get("descriptiveNonRepeating", {}) or {}
        link = dnr.get("record_link") or None
        if not link and dnr.get("record_ID"):
            link = f"https://collections.si.edu/search/detail/edanmdm:{dnr.get('record_ID')}"
        if not title or not link:
            continue
        out.append({
            "title": title,
            "unit": row.get("unitCode") or "",
            "record_url": link,
            "guid": dnr.get("guid"),
            "id": row.get("id"),
        })
    return out


def folkways_search(country: str, ethnicity: str, api_key: str, rows: int = 6) -> list[dict]:
    """Search Smithsonian Open Access for Folkways / CFCH audio records.

    Query ladder — each step is tried only until we've gathered `rows` results:
      1. `folkways "<ethnicity>"` — most specific.
      2. `folkways <country> music` — country-level fallback.
      3. `folkways <country_alias> music` — for historical names the catalog
         still uses (Myanmar records are typically filed under "Burma", etc.).

    De-duplicates by record URL across all queries."""
    queries = [f'folkways "{ethnicity}"', f'folkways {country} music']
    for alias in COUNTRY_ALIASES.get(country, []):
        queries.append(f'folkways {alias} music')

    seen_urls: set[str] = set()
    out: list[dict] = []
    for q in queries:
        if len(out) >= rows:
            break
        r = _get(SI_API, params={"q": q, "rows": rows, "api_key": api_key},
                         headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
        for rec in _si_extract_rows(r.json().get("response", {}).get("rows", [])):
            if rec["record_url"] in seen_urls:
                continue
            seen_urls.add(rec["record_url"])
            out.append(rec)
            if len(out) >= rows:
                break
        time.sleep(0.15)
    return out[:rows]


# =========================================================================
#  Bundle
# =========================================================================

def fetch_bundle(country: str, ethnicity: str, seed_traditions: list[str] | None = None,
                 folkways_api_key: str | None = None) -> dict:
    """Fetch Wikipedia + Commons + UNESCO ICH + Folkways for one ethnicity.

    Returns a JSON-serializable dict written to the media sidecar. Each source
    fails independently — if Wikipedia is down, we still get Commons + ICH."""
    bundle: dict[str, Any] = {
        "country": country,
        "ethnicity": ethnicity,
        "sources": {},
    }

    # --- Wikipedia
    try:
        wiki_title = wiki_resolve_title(ethnicity, country)
        if wiki_title:
            bundle["sources"]["wikipedia"] = wiki_fetch_article(wiki_title)
    except Exception as e:
        bundle["sources"]["wikipedia_error"] = str(e)

    # --- Commons
    try:
        wiki_title = None
        w = bundle["sources"].get("wikipedia")
        if w:
            wiki_title = w.get("title")
        # Feed seed traditions in as extra category hints (e.g. 'Suzani',
        # 'Longyi'). Commons often has dedicated categories for named textiles.
        bundle["sources"]["commons"] = commons_gather(
            ethnicity, country, extra_categories=(seed_traditions or []),
            wiki_title=wiki_title,
        )
    except Exception as e:
        bundle["sources"]["commons_error"] = str(e)

    # --- UNESCO ICH (filtered by relevance to this specific ethnicity)
    try:
        raw = unesco_ich_for_country(country)
        bundle["sources"]["unesco_ich"] = _filter_ich_by_relevance(raw, ethnicity, country)
    except Exception as e:
        bundle["sources"]["unesco_ich_error"] = str(e)

    # --- Folkways (only if key provided)
    if folkways_api_key:
        try:
            bundle["sources"]["folkways"] = folkways_search(country, ethnicity, folkways_api_key)
        except Exception as e:
            bundle["sources"]["folkways_error"] = str(e)

    return bundle
