"""Build data/index.json (and small per-page shards) from the library.

Consumed by the Astro site. Contents:

  data/index.json
      { regions, countries, ethnicities, all_objects_count, facets }

  data/globe.json
      lightweight lat/lon+preview payload for the world-map landing page

  data/ethnicities/<region>__<country>__<ethnicity>.json
      per-ethnicity page shard: writeup, tradition list, objects grouped by
      art form. Astro loads these on demand rather than one giant file.

  data/objects/<id>.json
      per-object detail page shard (full canonical record).

Idempotent: re-running rewrites all shard files based on current library.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import LIBRARY_DIR, DATA_DIR

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = REPO_ROOT / "content"

# R2 public base URL for images. Loaded from vault (via r2.py) or env var.
# r2.py imports boto3 at module load; catch that in case boto3 isn't installed.
# Env var overrides so `R2_PUBLIC_BASE=... python build_index.py` always works
# even in a bare virtualenv without boto3.
import os as _os

R2_PUBLIC_BASE = _os.environ.get("R2_PUBLIC_BASE", "").rstrip("/")
if not R2_PUBLIC_BASE:
    try:
        from folk_patterns.r2 import get_config as _r2_cfg
        R2_PUBLIC_BASE = (_r2_cfg().get("public_base") or "").rstrip("/")
    except Exception:
        # boto3 missing or vault unavailable — try reading public_base directly
        # from vault.toml so we don't need boto3 just for the URL.
        try:
            import tomllib as _tomllib
        except ImportError:
            import tomli as _tomllib  # type: ignore
        from pathlib import Path as _P
        _vault = _P(__file__).resolve().parents[2] / "tools" / "vault" / "vault.toml"
        if _vault.exists():
            _v = _tomllib.loads(_vault.read_text(encoding="utf-8"))
            R2_PUBLIC_BASE = ((_v.get("apis") or {}).get("cloudflare_r2") or {}).get("public_base_url", "").rstrip("/")


def _image_url(local_path: str | None) -> str | None:
    """Convert a local library path to a public R2 URL if configured, else fall back.

    local_path examples: 'library/central-asia/uzbekistan/uzbek/textile/suzani/images/foo.jpg'
    R2 keys are stored WITHOUT the leading 'library/' (upload_to_r2 strips it),
    so we strip it here too before building the R2 URL.
    """
    if not local_path:
        return None
    key = local_path.replace("\\", "/").lstrip("/")
    if key.startswith("library/"):
        key = key[len("library/"):]
    if R2_PUBLIC_BASE:
        return f"{R2_PUBLIC_BASE}/{key}"
    return "/library/" + key


# Perceptual image features for duplicate detection, cached by path, size and
# mtime in .cache/ (gitignored) — hashing ~3,700 images takes about a minute.
_HASH_CACHE_PATH = REPO_ROOT / ".cache" / "image_hashes.json"
_hash_cache: dict | None = None


def _image_features(local_path: str | None) -> list | None:
    """[16x16 dHash of the autocontrasted grey image, aspect ratio, mean RGB]."""
    global _hash_cache
    if not local_path:
        return None
    p = REPO_ROOT / local_path
    if not p.exists():
        return None
    if _hash_cache is None:
        try:
            _hash_cache = json.loads(_HASH_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _hash_cache = {}
    st = p.stat()
    ck = f"{local_path}|{st.st_size}|{int(st.st_mtime)}"
    if ck not in _hash_cache:
        from PIL import Image, ImageOps
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            return None
        g = ImageOps.autocontrast(im.convert("L"), cutoff=2).resize((17, 16), Image.LANCZOS)
        px = g.tobytes()
        h = sum(1 << i for i, (r, c) in enumerate((r, c) for r in range(16) for c in range(16))
                if px[r * 17 + c] > px[r * 17 + c + 1])
        _hash_cache[ck] = [str(h), im.width / im.height, list(im.resize((1, 1), Image.BOX).getpixel((0, 0)))]
    return _hash_cache[ck]


def _save_hash_cache() -> None:
    if _hash_cache is not None:
        _HASH_CACHE_PATH.parent.mkdir(exist_ok=True)
        _HASH_CACHE_PATH.write_text(json.dumps(_hash_cache), encoding="utf-8")


def _same_picture(a: list | None, b: list | None) -> bool:
    """Thresholds read off contact sheets of every same-ethnicity pair
    (2026-09-24): up to 12 of 256 bits every pair was the same photograph;
    from 12 to 24 only pairs with the same framing and colour were (spears shot
    on one museum backdrop differ in aspect or tint). An 8x8 hash without
    autocontrast is useless here — pale textiles on white hash identically."""
    if not a or not b:
        return False
    d = bin(int(a[0]) ^ int(b[0])).count("1")
    if d <= 12:
        return True
    aspect = abs(a[1] - b[1]) / max(a[1], b[1])
    colour = max(abs(x - y) for x, y in zip(a[2], b[2]))
    return d <= 24 and aspect <= 0.01 and colour <= 20


def slugify(s: str) -> str:
    from slugify import slugify as _s
    return _s(s)


def load_all_seeds() -> dict[str, dict]:
    """Return {region_slug: seed_dict}."""
    seeds: dict[str, dict] = {}
    for p in (DATA_DIR / "seed").glob("*.json"):
        s = json.loads(p.read_text(encoding="utf-8"))
        seeds[s["region"]] = s
    return seeds


def _ethnicity_key(region: str, country: str, ethnicity: str) -> str:
    return f"{slugify(region)}__{slugify(country)}__{slugify(ethnicity)}"


def build() -> None:
    seeds = load_all_seeds()

    # Index of ethnicity meta from seeds (homeland, tradition list, country).
    eth_meta: dict[str, dict] = {}
    # Reverse index: tradition (lowercased) -> [(region, country, ethnicity)].
    # Used to auto-route objects that came in with cultural=_regional but carry
    # a real tradition tag (e.g. "Tekke gul" is unambiguously Turkmen).
    tradition_owners: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for region, seed in seeds.items():
        for c in seed["countries"]:
            for eth in c["ethnicities"]:
                key = _ethnicity_key(region, c["country"], eth["name"])
                eth_meta[key] = {
                    "key": key,
                    "region": region,
                    "country": c["country"],
                    "ethnicity": eth["name"],
                    "homeland": eth.get("homeland"),
                    "homeland_place": eth.get("homeland_place"),
                    "seed_traditions": eth["traditions"],
                }
                for t in eth["traditions"]:
                    tradition_owners[t.strip().lower()].append((region, c["country"], eth["name"]))

    # Majority ethnicity per country — used as the fallback bucket for records
    # that were tagged with country=ethnicity or _regional and don't have a
    # more specific tradition tag. Mirrors media.COUNTRY_MAJORITY_ETHNICITY.
    # Derive country-majority routing from seed JSON. Adding a new ethnicity
    # becomes a single-file change (edit the seed) instead of touching
    # build_index + commons_arch + Places.
    _MAJORITY: dict[str, tuple[str, str]] = {}
    for region_slug, seed in seeds.items():
        for country in seed.get("countries", []):
            maj = country.get("majority_ethnicity")
            if maj:
                _MAJORITY[country["country"]] = (region_slug, maj)

    def _route_regional(rec: dict) -> tuple[str, str, str] | None:
        """Try to reattribute a _regional or country=ethnicity orphan record.

        Order of preference:
          1. Look up its `tradition` in the seed tradition owners map —
             preserves minority attribution when tradition is specific.
          2. Fall back to the country's majority ethnicity via _MAJORITY.
          3. Return None only when the country isn't in the majority map
             (e.g. Afghanistan, China (Xinjiang) — genuinely multi-ethnic)."""
        cul = rec.get("cultural") or {}
        trad = (cul.get("tradition") or "").strip().lower()
        if trad:
            owners = tradition_owners.get(trad, [])
            if len(owners) == 1:
                return owners[0]
            if len(owners) > 1:
                place = ((rec.get("location") or {}).get("made_in_place") or "").lower()
                for r, c, e in owners:
                    if c.lower().split()[0] in place:
                        return (r, c, e)
                # Ambiguous ownership + no place hint — default to the FIRST
                # seed-declared owner rather than dropping the record. For
                # cross-region traditions ("ikat" — 8+ owners) this lands on
                # whichever ethnicity ordered it first in their traditions
                # list. Preserves the record on the map (better than losing
                # it) at the cost of some attribution fuzziness.
                return owners[0]
        # Country-majority fallback (for _regional this returns None since
        # _regional isn't a real country name).
        country = cul.get("country") or ""
        maj = _MAJORITY.get(country)
        if maj:
            return (maj[0], country, maj[1])
        return None

    # Walk the library. Aggregate objects per ethnicity.
    objects_by_eth: dict[str, list[dict]] = defaultdict(list)
    global_facets = {"art_form": defaultdict(int), "source": defaultdict(int),
                     "country": defaultdict(int)}
    all_objects_count = 0
    reroute_stats = {"routed": 0, "unroutable": 0, "junk_drop": 0, "classifier_override": 0, "classifier_reject": 0, "image_unusable": 0, "reattributed": 0, "cross_culture_dup": 0}

    # Per-record classifier overrides (data/classifier_overrides.json).
    # Populated by scripts/expand_classifier.py — Claude assigns an art_form
    # to individual records that landed in "unclassified" via the rule-based
    # classifier. Value "reject" means the record is actually junk (bird
    # binomial, wrong country, etc.) and should be dropped.
    _classifier_overrides: dict[str, str] = {}
    _overrides_path = DATA_DIR / "classifier_overrides.json"
    if _overrides_path.exists():
        try:
            _classifier_overrides = json.loads(_overrides_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Build-time safety net: re-run the current junk filter on every loaded
    # record so patterns extended AFTER scrape (Diospyros-style Latin binomials,
    # YYYY-MM-DD-HHMMSS camera dumps, Flickr batch IDs) get filtered out even
    # for records already in the library. Cheaper than re-scraping when we
    # tighten the junk regexes.
    from folk_patterns.junk import should_reject as _should_reject

    for meta_path in sorted(LIBRARY_DIR.glob("*/*/*/*/*/metadata.json")):
        records = json.loads(meta_path.read_text(encoding="utf-8"))
        for rec in records:
            cul = rec.get("cultural") or {}
            src = (rec.get("source") or {}).get("museum", "")
            # Safety-net junk gate. Apply to ALL sources — trusted museum
            # APIs still leak Latin binomials via cross-department search
            # (Smithsonian's "batak" query returns Philippine botanical
            # specimens). Regex patterns (binomials, camera dumps, coats-of-
            # arms) are unambiguous enough that even trusted sources should be
            # filtered when they leak these.
            _title = (rec.get("physical") or {}).get("title") or ""
            _desc = (rec.get("physical") or {}).get("summary") or ""
            _prov = (rec.get("source") or {}).get("museum_name") or ""
            _junk, _why = _should_reject(_title, _desc, _prov)
            if _junk:
                reroute_stats["junk_drop"] += 1
                continue
            # The vetter's verdict is final for every source. Museum-curated
            # APIs and curated monument categories used to bypass it, when an
            # earlier prompt false-rejected religious sculpture and monuments;
            # read on 2026-09-24, the current prompt's 64 drops from V&A, Met,
            # Cleveland, Smithsonian and Rijks were right (docs/vetting.md).
            # A drop that belongs to another atlas culture was re-judged under
            # that culture by scripts/reattribute_drops.py; a YES files it
            # there, with the re-judge's own category, image and era verdicts.
            _re_attr = cul.get("reattribution") or {}
            if cul.get("vision_vetted") is False and _re_attr.get("belongs") and _re_attr.get("to") in eth_meta:
                _to = eth_meta[_re_attr["to"]]
                cul.update({"region": _to["region"], "country": _to["country"],
                            "ethnicity": _to["ethnicity"],
                            "vision_image": _re_attr.get("image") or cul.get("vision_image"),
                            "vision_era": _re_attr.get("era") or cul.get("vision_era"),
                            "vision_reason": _re_attr.get("reason") or cul.get("vision_reason")})
                if _re_attr.get("art_form"):
                    cul["art_form_vision"] = _re_attr["art_form"]
                reroute_stats["reattributed"] += 1
            elif cul.get("vision_vetted") is False:
                continue
            # The vetter judged the picture itself unreadable (blank,
            # placeholder, scale bar only) — nothing to show.
            if cul.get("vision_image") == "unusable":
                reroute_stats["image_unusable"] += 1
                continue
            # If vision assigned an art_form, prefer it over the rule-based one.
            if cul.get("art_form_vision"):
                cul["art_form"] = cul["art_form_vision"]
            # Apply per-record classifier overrides from
            # data/classifier_overrides.json — only to records the vetter has
            # not judged: its category is newer and, where the two differ,
            # better (a lute the overrides call household, a yurt photo they
            # call architectural). "reject" drops the record.
            _override = None if cul.get("vision_image") else _classifier_overrides.get(rec.get("id") or "")
            if _override:
                if _override == "reject":
                    reroute_stats["classifier_reject"] += 1
                    continue
                cul["art_form"] = _override
                reroute_stats["classifier_override"] += 1
            region = cul.get("region")
            country = cul.get("country")
            ethnicity = cul.get("ethnicity")
            if not (region and country and ethnicity):
                continue
            # Auto-reattribute _regional records AND country=ethnicity
            # orphans (Malaysia/Malaysia, Vietnam/Vietnam, ...) into concrete
            # ethnicity buckets so they show under a real globe marker.
            if country == "_regional" or ethnicity == "_regional" or country == ethnicity:
                routed = _route_regional(rec)
                if not routed:
                    reroute_stats["unroutable"] += 1
                    continue  # drop from index — no map marker for the unroutable
                region, country, ethnicity = routed
                # Rewrite the cultural fields in-place so downstream shard export
                # sees the corrected attribution.
                rec.setdefault("cultural", {}).update({
                    "region": region, "country": country, "ethnicity": ethnicity,
                })
                reroute_stats["routed"] += 1
            key = _ethnicity_key(region, country, ethnicity)
            objects_by_eth[key].append(rec)

    # One culture per object. 145 museum objects sit in the library under two
    # cultures (a Cleveland rug under both Afghan Turkmen and Turkmen, a BM
    # robe under Hazara and Uzbek) because two cultures' searches found it.
    # Keep the copy whose country the object's own place text names, else the
    # first in path order; the others are dropped from the index.
    copies: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for key in sorted(objects_by_eth):
        for rec in objects_by_eth[key]:
            copies[rec.get("id") or f"{key}#{id(rec)}"].append((key, rec))
    for rid, cs in copies.items():
        if len(cs) < 2:
            continue
        def _place_hit(kr):
            key, rec = kr
            place = " ".join(str(x) for x in ((rec.get("location") or {}).get("made_in_place"),
                                              (rec.get("physical") or {}).get("summary")) if x).lower()
            return eth_meta[key]["country"].split(" (")[0].lower() in place
        keep = next((kr for kr in cs if _place_hit(kr)), cs[0])
        for key, rec in cs:
            if rec is not keep[1]:
                objects_by_eth[key] = [r for r in objects_by_eth[key] if r is not rec]
                reroute_stats["cross_culture_dup"] += 1
    for key, recs in objects_by_eth.items():
        for rec in recs:
            cul = rec.get("cultural") or {}
            global_facets["art_form"][cul.get("art_form") or "unclassified"] += 1
            global_facets["source"][(rec.get("source") or {}).get("museum") or "?"] += 1
            global_facets["country"][eth_meta[key]["country"]] += 1
            all_objects_count += 1

    print(f"Re-attribution: routed {reroute_stats['routed']} previously _regional records; "
          f"{reroute_stats['unroutable']} could not be attributed and were dropped from the map. "
          f"Junk filter caught {reroute_stats['junk_drop']} pre-existing junk records. "
          f"Classifier overrides: {reroute_stats['classifier_override']} reclassified, "
          f"{reroute_stats['classifier_reject']} rejected. "
          f"Unusable images dropped: {reroute_stats['image_unusable']}. "
          f"Drops re-filed under another culture: {reroute_stats['reattributed']}. "
          f"Second copies of an object filed under two cultures dropped: {reroute_stats['cross_culture_dup']}.")

    # Build the globe payload (lightweight).
    globe_points: list[dict] = []
    for key, meta in eth_meta.items():
        if not meta.get("homeland"):
            continue
        objs = objects_by_eth.get(key) or []
        # pick a top image (highest pattern_density, then first)
        objs_sorted = sorted(objs, key=lambda r: -(r.get("cultural", {}).get("pattern_density") or 0))
        top_image = None
        for r in objs_sorted:
            for img in r.get("images") or []:
                if img.get("local_path"):
                    top_image = _image_url(img["local_path"])
                    break
            if top_image:
                break
        globe_points.append({
            "key": key,
            "region": meta["region"],
            "country": meta["country"],
            "ethnicity": meta["ethnicity"],
            "homeland_place": meta.get("homeland_place"),
            "lat": meta["homeland"]["lat"],
            "lon": meta["homeland"]["lon"],
            "object_count": len(objs),
            "seed_traditions": meta["seed_traditions"][:6],
            "top_image": top_image,
        })

    # Write shards. Wipe first so records that were dropped by junk /
    # classifier / attribution filters this build don't linger as orphan
    # shard files from a previous, more permissive run.
    out_root = DATA_DIR
    for sub in ("ethnicities", "objects"):
        d = out_root / sub
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    # Load writeups if present so shard can embed them.
    def _load_writeup(region: str, country: str, ethnicity: str) -> str | None:
        wp = CONTENT_DIR / slugify(region) / f"{slugify(country)}__{slugify(ethnicity)}.md"
        if wp.exists():
            return wp.read_text(encoding="utf-8")
        return None

    # Load the media sidecar (Commons photos / UNESCO ICH / Folkways refs).
    # We only surface a slim subset to keep shard JSON small.
    def _load_media(region: str, country: str, ethnicity: str) -> dict:
        p = CONTENT_DIR / "media" / slugify(region) / f"{slugify(country)}__{slugify(ethnicity)}.json"
        if not p.exists():
            return {}
        b = json.loads(p.read_text(encoding="utf-8"))
        srcs = b.get("sources") or {}
        wiki = srcs.get("wikipedia") or {}
        raw_commons = srcs.get("commons") or []
        # Drop photos that failed the vision-vetting pass (vetted == False).
        # Photos not yet vetted (vetted missing) are kept — a not-yet-run pass
        # shouldn't blank the gallery.
        commons = [c for c in raw_commons if c.get("vetted") is not False]
        ich = srcs.get("unesco_ich") or []
        folkways = srcs.get("folkways") or []
        return {
            "wikipedia_url": wiki.get("url"),
            "wikipedia_title": wiki.get("title"),
            "commons_photos": [
                {
                    "title": p_.get("title"),
                    "thumb_url": p_.get("thumb_url"),
                    "page_url": p_.get("page_url"),
                    "credit": p_.get("credit"),
                    "license": p_.get("license"),
                    "description": p_.get("description"),
                    "source_category": p_.get("source_category"),
                }
                for p_ in commons
            ],
            "unesco_ich": [
                {
                    "code": e.get("code"),
                    "title": e.get("title"),
                    "description": e.get("description"),
                    "unesco_url": e.get("unesco_url"),
                    "commons_category": e.get("commons_category"),
                }
                for e in ich
            ],
            "folkways": [
                {
                    "title": f.get("title"),
                    "unit": f.get("unit"),
                    "record_url": f.get("record_url"),
                }
                for f in folkways
            ],
        }

    # Per-ethnicity shard
    shown_count: dict[str, int] = {}
    for key, meta in eth_meta.items():
        objs = objects_by_eth.get(key) or []
        # bucket by art_form
        by_af: dict[str, list[dict]] = defaultdict(list)
        for r in objs:
            by_af[(r.get("cultural") or {}).get("art_form") or "unclassified"].append(r)
        # keep only lightweight fields per object in the shard (id, title,
        # date, art_form, pattern_density, first image path, source url)
        def _slim(r):
            imgs = r.get("images") or []
            img_path = None
            for i in imgs:
                if i.get("local_path"):
                    img_path = _image_url(i["local_path"])
                    break
            phys = r.get("physical") or {}
            src = r.get("source") or {}
            # Highlight score = "how much unique metadata this record has".
            # Records with a title + summary + credit outrank blank V&A
            # serial-fragment stubs (many have title=None). Higher first.
            score = 0
            if phys.get("title"): score += 2
            if phys.get("summary"): score += 3
            if src.get("credit_line"): score += 1
            if phys.get("materials"): score += 1
            if phys.get("date_earliest"): score += 1
            if phys.get("dimensions_note"): score += 1
            # Cross-source diversity — prefer non-V&A when mixing so V&A serials
            # don't monopolise the top-6.
            src_bonus = {"cleveland": 3, "smithsonian": 3, "met": 3,
                         "commons_arch": 1, "europeana": 2, "va": 0,
                         "commons": 0}.get(src.get("museum", ""), 1)
            score += src_bonus
            # A WEAK image (subject a backdrop, obscured, too partial) still
            # belongs but should never lead a gallery — sink it below every
            # readable image.
            if (r.get("cultural") or {}).get("vision_image") == "weak":
                score -= 100
            local = next((i["local_path"] for i in imgs if i.get("local_path")), None)
            acc = (src.get("accession_number") or "").strip()
            return {
                "id": r.get("id"),
                "title": phys.get("title") or phys.get("classification"),
                "date_text": phys.get("date_text"),
                "art_form": (r.get("cultural") or {}).get("art_form"),
                "tradition": (r.get("cultural") or {}).get("tradition"),
                "pattern_density": (r.get("cultural") or {}).get("pattern_density"),
                "era": (r.get("cultural") or {}).get("vision_era") or None,
                "image_quality": (r.get("cultural") or {}).get("vision_image") or None,
                "source": src.get("museum"),
                "object_url": src.get("object_url"),
                "image": img_path,
                "place": (r.get("location") or {}).get("made_in_place"),
                # Tile-ordering helpers — not surfaced in the UI directly
                "_score": score,
                "_acc": f"{src.get('museum')}|{acc}" if acc else None,
                "_hash": _image_features(local),
            }

        def _dedup_and_rank(items: list[dict]) -> list[dict]:
            """Two passes:
              1. Duplicate collapse — the same museum accession number, or the
                 same picture (_same_picture). Titles are NOT a duplicate
                 signal: generic ones ("adire", "cloth", "kanga",
                 "photographic print; album") are shared by dozens of distinct
                 objects, and a title fingerprint once hid 1,470 of 3,744.
              2. Interleave traditions for the flat gallery view."""
            ranked = sorted(items, key=lambda x: -(x.get("_score") or 0))
            kept: list[dict] = []
            seen_acc: set[str] = set()
            for it in ranked:
                if it.get("_acc") and it["_acc"] in seen_acc:
                    continue
                if any(_same_picture(it.get("_hash"), k.get("_hash")) for k in kept):
                    continue
                if it.get("_acc"):
                    seen_acc.add(it["_acc"])
                kept.append(it)
            # Pass 2: interleave by TRADITION so a top-N view naturally shows
            # one representative per sub-category (Bibi-Khanym once, Chor Minor
            # once, Registan once, ...) before circling back for a second pass.
            # The frontend renders a flat gallery per art_form, so this
            # ordering is what the user sees first.
            by_trad: dict[str, list[dict]] = defaultdict(list)
            for it in kept:
                by_trad[(it.get("tradition") or "").lower() or "?"].append(it)
            interleaved: list[dict] = []
            while any(by_trad.values()):
                for t in list(by_trad.keys()):
                    if by_trad[t]:
                        interleaved.append(by_trad[t].pop(0))
            return [{k: v for k, v in it.items() if not k.startswith("_")} for it in interleaved]
        writeup_md = _load_writeup(meta["region"], meta["country"], meta["ethnicity"])
        media = _load_media(meta["region"], meta["country"], meta["ethnicity"])

        # Fallback: if the museum-object gallery is empty or very thin, promote
        # a few curated Wikipedia-Commons photos into a "photo" gallery bucket.
        # This is documentary imagery, not museum artefacts, but it's better
        # than an empty gallery for small minorities not well-represented in
        # Western museum collections.
        art_form_buckets = {
            af: _dedup_and_rank([_slim(r) for r in recs])
            for af, recs in by_af.items()
        }
        real_object_count = len(objs)
        if real_object_count < 5:
            promo = []
            for i, cp in enumerate((media.get("commons_photos") or [])[:8]):
                if not cp.get("thumb_url"):
                    continue
                promo.append({
                    "id": f"commons-{key}-{i}",
                    "title": cp.get("title"),
                    "date_text": None,
                    "art_form": "photo",
                    "tradition": "documentary photograph",
                    "pattern_density": None,
                    "source": "commons",
                    "object_url": cp.get("page_url"),
                    "image": cp.get("thumb_url"),
                    "place": None,
                })
            if promo:
                art_form_buckets["photo"] = promo

        shard = {
            "key": key,
            "region": meta["region"],
            "country": meta["country"],
            "ethnicity": meta["ethnicity"],
            "homeland": meta.get("homeland"),
            "homeland_place": meta.get("homeland_place"),
            "seed_traditions": meta["seed_traditions"],
            # What the galleries show, so the panel header matches them.
            "object_count": sum(len(v) for v in art_form_buckets.values()),
            "writeup_markdown": writeup_md,
            "art_form_buckets": art_form_buckets,
            "wikipedia_url": media.get("wikipedia_url"),
            "wikipedia_title": media.get("wikipedia_title"),
            "commons_photos": media.get("commons_photos", []),
            "unesco_ich": media.get("unesco_ich", []),
            "folkways": media.get("folkways", []),
        }
        (out_root / "ethnicities" / f"{key}.json").write_text(
            json.dumps(shard, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        shown_count[key] = shard["object_count"]

    # Per-object shard (canonical record — but strip `raw` which contains the
    # full museum API response and can be 10-100KB per record). The frontend
    # only needs `physical`, `source`, `cultural`, `location`, `attribution`,
    # `linked_data`, `images` — `raw` is diagnostic-only and lives in the
    # library for reference. Also rewrite images[].local_path to `url` (R2
    # public URL) so the site never reads library/ at runtime.
    for objs in objects_by_eth.values():
        for r in objs:
            oid = r.get("id")
            if not oid:
                continue
            # Copy everything EXCEPT `raw` — that's the fat one.
            r2rec = {k: v for k, v in r.items() if k != "raw"}
            new_imgs = []
            for img in r.get("images") or []:
                img2 = dict(img)
                if img.get("local_path"):
                    img2["url"] = _image_url(img["local_path"]) or img.get("url")
                new_imgs.append(img2)
            r2rec["images"] = new_imgs
            (out_root / "objects" / f"{oid}.json").write_text(
                json.dumps(r2rec, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    # Top-level index
    index = {
        "regions": sorted(seeds.keys()),
        "countries_by_region": {
            region: [c["country"] for c in seed["countries"]]
            for region, seed in seeds.items()
        },
        "ethnicity_keys": sorted(eth_meta.keys()),
        "all_objects_count": all_objects_count,
        "facets": {
            "art_form": dict(global_facets["art_form"]),
            "source": dict(global_facets["source"]),
            "country": dict(global_facets["country"]),
        },
    }
    (out_root / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # Markers carry the same count as the panel: objects left after dedup.
    for gp in globe_points:
        gp["object_count"] = shown_count.get(gp["key"], gp["object_count"])
    (out_root / "globe.json").write_text(
        json.dumps({"points": globe_points}, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    _save_hash_cache()
    print(f"Wrote data/index.json  ({all_objects_count} objects, {len(globe_points)} globe points)")
    print(f"Wrote {len(eth_meta)} ethnicity shards, {sum(len(v) for v in objects_by_eth.values())} object shards")


if __name__ == "__main__":
    build()
