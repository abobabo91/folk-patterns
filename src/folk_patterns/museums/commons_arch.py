"""Wikimedia Commons — architectural photo scraper for named buildings.

For each ethnicity we maintain a curated list of Commons categories covering
their signature architecture (mosques, temples, longhouses, tongkonan, etc.).
Fetch top images ≥ 800px, download to library, then vision-vet during the
normal vet_images pipeline to drop satellite/aerial/landscape false positives
that the raw category browse surfaces.

This unlocks the "muqarnas ceiling / tile wall" gap that pure museum-object
scrapers cannot fill — those images live in mosques, not museums."""
from __future__ import annotations

import json

from ..util import RateLimitedClient, download_image, append_metadata, library_path, DATA_DIR
from ..classify import classify
from ..rejects import log_reject


def _art_form_for_category(cat: str, title: str, description: str) -> str:
    """Pick the right art_form bucket for a Commons record.

    The category name is the strongest signal — if we scraped from "Cuisine
    of Uzbekistan" we KNOW the record is food, regardless of title. If from
    "Interior of X Mosque" we know it's architectural. Fall back to the
    rule-based classifier for ambiguous category names.
    """
    c = (cat or "").lower()
    # Category-name → art_form overrides (checked in order)
    HINTS = [
        ("household", ("cuisine of", "food of", "bread of", "cooking")),
        ("photo",     ("people", "person", "culture of", "society of",
                       "portraits of", "portrait of", "photographs",
                       "nowruz", "wedding", "festival", "celebration",
                       "dance", "music of", "musicians", "dancers",
                       "religion of", "language")),
        ("garment",   ("traditional clothing", "traditional costume",
                       "traditional dress", "costume of", "clothing of")),
        ("architectural", ("mosque", "madrasa", "madrasah", "mausoleum",
                           "minaret", "temple", "pagoda", "shrine", "palace",
                           "kraton", "monastery", "cathedral", "chapel",
                           "fortress", "gateway", "gate", "tower",
                           "borobudur", "prambanan", "registan", "shwedagon",
                           "chor minor", "bibi-khany", "itchan kala", "ichan kala",
                           "shah-i-zinda", "kalyan", "poi kalyan", "gur-e amir",
                           "tilya-kori", "sher-dor", "ulugh beg", "ark of bukhara",
                           "interior of", "dome of", "domes of", "domes in",
                           "muqarnas", "ceilings", "ceiling paintings",
                           "majolica", "mosaics", "tilework", "brick wall",
                           "wall textures",
                           # Bare "architecture" catches "Berber architecture",
                           # "Islamic architecture", "Balinese architecture"
                           # etc — was defaulting to photo bucket.
                           "architecture", "architectural",
                           "longhouses", "rumah", "tongkonan", "yurts",
                           "kasbah", "medina of", "old city", "ancient",
                           # Named heritage sites
                           "kala", "ribat", "kraton", "wat ", "prasat",
                           "khosro abad", "aït benhaddou", "gedung sate",
                           "kampung", "ta prohm", "akdamar", "dungur")),
    ]
    for af, kws in HINTS:
        if any(k in c for k in kws):
            return af
    # Fallback to rule-based classifier on the title (+ description if useful)
    cf = classify({
        "classification": title or "", "object_type": title or "",
        "title": title or "", "medium": "", "material_technique": "",
        "summary": description or "", "description": description or "",
    })
    af = cf["art_form"]
    # If classifier returned "unclassified", default to "photo" — most
    # unclassified Commons categories with title-less records are people /
    # scenery / documentary photos.
    return af if af != "unclassified" else "photo"


def _load_arch_categories_from_seeds() -> dict[str, list[str]]:
    """Load per-ethnicity architectural Commons categories from
    data/seed/*.json (the `arch_commons_categories` field on each ethnicity).
    Falls back to the empty dict if seeds haven't been migrated yet.

    This replaces the previously-inline ARCH_CATEGORIES dict so adding
    architecture for a new ethnicity is a one-file diff (the seed) instead
    of touching this scraper too."""
    out: dict[str, list[str]] = {}
    for seed_path in (DATA_DIR / "seed").glob("*.json"):
        try:
            seed = json.loads(seed_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for country in seed.get("countries") or []:
            for eth in country.get("ethnicities") or []:
                cats = eth.get("arch_commons_categories") or []
                if cats:
                    out[eth["name"]] = cats
    return out


# Curated architectural Commons categories per ethnicity. Loaded at import
# from the seed JSON files so adding architecture for a new ethnicity is a
# one-file diff. Grouped by distinctive built-heritage terms. Missing
# ethnicity = no signature architecture worth mining from Commons.
ARCH_CATEGORIES: dict[str, list[str]] = _load_arch_categories_from_seeds()

# Kept as an inline backup — the hand-curated list below was the source
# before the seed migration on 2026-07-23. If seed loading is empty for
# any reason (broken seed file, dev environment without data/seed), fall
# back to this dict so scraping still works.
_LEGACY_ARCH_CATEGORIES: dict[str, list[str]] = {
    # Central Asia
    "Uzbek": [
        "Bibi-Khanym Mosque", "Shah-i-Zinda", "Kalyan minaret",
        "Poi Kalyan", "Registan", "Ulugh Beg Madrasah (Samarkand)",
        "Tilya-Kori Madrasah", "Sher-Dor Madrasah",
        "Ark of Bukhara", "Chor Minor", "Char Minar Bukhara",
        "Islom Xoja Minaret", "Ichan Kala", "Itchan Kala",
        "Mausoleum of Amir Timur", "Gur-e Amir",
        "Kok Gumbaz", "Poi Kalan", "Miri Arab Madrasah",
    ],
    "Karakalpak": ["Mizdakhan", "Ayaz Kala", "Toprak Kala"],
    "Bukharan Jew": ["Synagogues in Uzbekistan"],
    "Kazakh": ["Kazakh yurts", "Mausoleum of Khoja Ahmed Yasawi"],
    "Kyrgyz": ["Boz Uy", "Yurts in Kyrgyzstan", "Burana Tower"],
    "Turkmen": ["Ancient Merv", "Kunya-Urgench", "Turkmen yurts"],
    "Tajik": ["Panjakent", "Hisor Fortress", "Tajik houses"],
    "Pamiri": ["Pamiri house", "Wakhi houses"],
    "Uyghur": [
        "Id Kah Mosque", "Emin Minaret", "Apak Khoja Mausoleum",
        "Islamic architecture in Xinjiang",
    ],
    "Kazakh (Xinjiang)": ["Yurts in Xinjiang"],
    "Hazara": ["Bamyan Buddhas", "Hazara villages"],
    "Afghan Turkmen": ["Turkmen yurts"],
    "Uzbek (Afghanistan)": ["Mazar-e Sharif"],

    # SE Asia
    "Bamar": [
        "Shwedagon Pagoda", "Ananda Temple", "Shwezigon Pagoda",
        "Dhammayangyi Temple", "Sulamani Temple", "Thatbyinnyu Temple",
        "Shwenandaw Kyaung", "Kuthodaw Pagoda", "Bagan temples",
        "Mahamuni Buddha Temple",
    ],
    "Chin": ["Chin State", "Traditional houses in Chin State"],
    "Khmer": [
        "Angkor Wat", "Bayon", "Ta Prohm", "Preah Vihear Temple",
        "Banteay Srei", "Angkor Thom", "Roluos Group",
        "Silver Pagoda, Phnom Penh", "Wat Phnom",
    ],
    "Lao": [
        "Pha That Luang", "Haw Phra Kaew", "Wat Xieng Thong",
        "Wat Sisaket", "Vat Phou", "Traditional houses in Laos",
    ],
    "Lao Isan": ["Prasat Hin Phimai", "Wat Phra That Phanom", "Wat Nong Wang"],
    "Thai": [
        "Wat Phra Kaew", "Wat Arun", "Wat Pho",
        "Ayutthaya Historical Park", "Sukhothai Historical Park",
        "Wat Phra Si Sanphet", "Wat Chaiwatthanaram",
    ],
    "Kinh": [
        "Imperial City, Huế", "Perfume Pagoda", "One Pillar Pagoda",
        "Temple of Literature, Hanoi", "Bái Đính Temple",
        "Hoi An Ancient Town", "Old Quarter, Hanoi",
    ],
    "Cham": ["My Son", "Po Nagar Cham Towers", "Po Klong Garai",
             "Cham towers"],
    "Hmong": ["Hmong villages"],
    "Malay": [
        "Sultan Abu Bakar State Mosque",
        "Sultan Salahuddin Abdul Aziz Mosque",
        "Malay traditional houses", "Kampong houses in Malaysia",
        "Istana Alam Shah", "Kuala Kangsar",
    ],
    "Iban": ["Iban longhouses", "Rumah panjang", "Longhouses in Sarawak"],
    "Javanese": [
        "Borobudur", "Prambanan", "Kraton Yogyakarta",
        "Kraton Surakarta", "Sewu Temple", "Mendut Temple",
        "Traditional Javanese houses", "Joglo",
    ],
    "Balinese": [
        "Pura Besakih", "Pura Tanah Lot", "Pura Ulun Danu Bratan",
        "Goa Gajah", "Uluwatu Temple", "Balinese architecture",
        "Bali Aga villages",
    ],
    "Sundanese": ["Sundanese houses", "Ciamis Regency vernacular"],
    "Batak": ["Batak houses", "Rumah Bolon", "Ambarita",
              "Bawomataluo"],
    "Minangkabau": ["Rumah Gadang", "Istana Basa Pagaruyung",
                    "Minangkabau vernacular architecture"],
    "Dayak": ["Rumah Betang", "Dayak longhouses", "Dayak villages",
              "Traditional Dayak architecture"],
    "Toraja": ["Tongkonan", "Toraja villages", "Kete Kesu",
               "Toraja funeral"],
    "Filipino": [
        "Bahay na bato", "Nipa hut", "Baguio Cathedral",
        "Vigan", "Paoay Church", "Miagao Church",
    ],
    "T'boli": ["T'boli people", "Lake Sebu"],
    "Yakan": ["Yakan people"],
}


# If seed loading returned an empty dict (dev environment, etc.), fall back
# to the legacy hardcoded list so scraping still works without seeds.
if not ARCH_CATEGORIES:
    ARCH_CATEGORIES = _LEGACY_ARCH_CATEGORIES


def _fetch_category_files(client: RateLimitedClient, category: str, limit: int = 25) -> list[dict]:
    """List files in a Commons category with imageinfo. Uses the shared
    RateLimitedClient (polite spacing) so Wikimedia doesn't 429 us."""
    j = client.get_json(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query", "generator": "categorymembers",
            "gcmtitle": f"Category:{category}", "gcmtype": "file",
            "gcmlimit": limit,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|size|mime",
            "iiurlwidth": 1200, "format": "json",
        },
    )
    out: list[dict] = []
    for _, p in (j.get("query") or {}).get("pages", {}).items():
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii:
            continue
        mime = ii.get("mime", "")
        if not mime.startswith("image/") or mime == "image/svg+xml":
            continue
        if (ii.get("width") or 0) < 800:
            continue
        meta = ii.get("extmetadata", {}) or {}
        title = p.get("title", "").replace("File:", "")
        out.append({
            "title": title,
            "url": ii.get("thumburl") or ii.get("url"),
            "full_url": ii.get("url"),
            "page_url": f"https://commons.wikimedia.org/wiki/{p.get('title','')}",
            "credit": _strip(meta.get("Artist", {}).get("value")) if meta.get("Artist") else None,
            "license": (meta.get("LicenseShortName") or {}).get("value"),
            "description": _strip((meta.get("ImageDescription") or {}).get("value")),
            "width": ii.get("width"), "height": ii.get("height"),
            "source_category": category,
        })
    return out


def _strip(s):
    if not s: return None
    import re
    return re.sub(r"<[^>]+>", "", str(s)).strip() or None


def _to_canonical(rec: dict, cultural: dict) -> dict:
    from ..schema import _empty_record
    # Stable-ish id: sha1(URL) short prefix so re-runs don't duplicate.
    import hashlib
    oid = hashlib.sha1((rec.get("url") or rec.get("title") or "").encode()).hexdigest()[:16]
    r = _empty_record("commons_arch", oid)
    r["cultural"].update(cultural)

    r["physical"]["title"] = rec.get("title")
    r["physical"]["summary"] = rec.get("description")
    r["physical"]["classification"] = "Architecture / photograph"

    r["source"]["museum_name"] = "Wikimedia Commons"
    r["source"]["object_url"] = rec.get("page_url")
    r["source"]["credit_line"] = rec.get("credit")
    lic = (rec.get("license") or "").lower()
    if "cc0" in lic or "public domain" in lic:
        r["source"]["rights"] = "public-domain"
    else:
        r["source"]["rights"] = rec.get("license") or "cc"

    r["images"].append({
        "url": rec.get("url"), "iiif_id": None, "iiif_base": None,
        "role": "primary", "sha256": None, "bytes": None, "local_path": None,
    })
    r["raw"] = rec
    return r


def scrape_ethnicity(client: RateLimitedClient, region: str, country: str,
                     ethnicity: str, max_per_category: int = 8,
                     max_total: int = 40) -> int:
    from ..junk import should_reject
    cats = ARCH_CATEGORIES.get(ethnicity, [])
    if not cats:
        return 0
    saved = 0
    seen_urls: set[str] = set()
    for cat in cats:
        if saved >= max_total:
            break
        try:
            files = _fetch_category_files(client, cat, limit=max_per_category)
        except Exception as e:
            print(f"  ! commons_arch cat={cat!r} failed: {e}", flush=True)
            continue
        for f in files[:max_per_category]:
            if saved >= max_total:
                break
            url = f.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            # Ingest-time junk gate: catches landscape / camera-dump
            # filenames (GOPR12345, IMG_0001, YYYYMMDD_xxxx, aerial photos)
            # BEFORE we spend a download + vet call on them.
            is_junk, junk_reason = should_reject(f.get("title", ""), f.get("description") or "", "Wikimedia Commons")
            if is_junk:
                log_reject(source="commons_arch", reason=f"junk-{junk_reason}",
                           region=region, country=country, ethnicity=ethnicity,
                           tradition=cat, title=f.get("title"))
                continue
            # Category-name-driven classification. Was hardcoded to
            # "architectural" — resulted in food photos ("Cuisine of Uzbekistan"
            # → Karam do'lma), toys ("Culture of Uzbekistan" → an old-man toy)
            # and portraits landing in the architectural bucket.
            af = _art_form_for_category(cat, f.get("title", ""), f.get("description") or "")
            cultural = {
                "region": region, "country": country, "ethnicity": ethnicity,
                "tradition": cat, "art_form": af,
                "pattern_density": 2 if af == "architectural" else 1,
            }
            rec = _to_canonical(f, cultural)
            dest = library_path(region, country, ethnicity, af, cat[:40])
            fname = f"carch_{rec['id'].split('-',1)[-1]}.jpg"
            dst = dest / "images" / fname
            try:
                sha, size = download_image(client, url, dst)
                rec["images"][0]["sha256"] = sha
                rec["images"][0]["bytes"] = size
                rec["images"][0]["local_path"] = str(dst.relative_to(dest.parents[5]))
            except Exception as e:
                print(f"  ! commons_arch img download failed: {e}", flush=True)
                continue
            append_metadata(dest, rec)
            saved += 1
    return saved
