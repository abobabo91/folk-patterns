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
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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
    # Derive country-majority routing + arch-monument trust set from seed
    # JSON. Adding a new ethnicity becomes a single-file change (edit the
    # seed) instead of touching build_index + commons_arch + Places.
    _MAJORITY: dict[str, tuple[str, str]] = {}
    _ARCH_MONUMENT_TRADITIONS: set[str] = set()
    for region_slug, seed in seeds.items():
        for country in seed.get("countries", []):
            maj = country.get("majority_ethnicity")
            if maj:
                _MAJORITY[country["country"]] = (region_slug, maj)
            for eth in country.get("ethnicities", []):
                for cat in eth.get("arch_commons_categories") or []:
                    _ARCH_MONUMENT_TRADITIONS.add(cat.strip().lower())

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
    reroute_stats = {"routed": 0, "unroutable": 0, "junk_drop": 0, "classifier_override": 0, "classifier_reject": 0}

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
            # Museum-curated APIs whose vision-vet FALSE flags we IGNORE
            # entirely — curator-vetted at source; vision false-rejects legit
            # religious sculpture (Buddha statues, dakinis, Shiva panels).
            _TRUSTED_MUSEUM_SOURCES = {"va", "met", "cleveland", "smithsonian", "rijks"}
            # For commons_arch, trust vision-vet UNLESS the tradition is
            # a hand-curated architectural monument (an entry in some
            # ethnicity's `arch_commons_categories` in the seed JSON).
            # Village-name traditions like "Ambarita" catch a lot of
            # tourist/landscape junk that vision correctly rejects;
            # monument-category traditions are inherently about a specific
            # building and vision was over-rejecting legit views of them.
            trad_lower = (cul.get("tradition") or "").lower()
            _is_arch_monument = trad_lower in _ARCH_MONUMENT_TRADITIONS
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
            # Drop records the vision pass explicitly flagged as not authentic
            # material folk culture — BUT only for uncurated sources. Museum-
            # curated APIs (V&A, Met, Cleveland, Smithsonian) are already
            # source-vetted; Claude's strict prompt often false-rejects
            # legitimate religious sculpture (Buddha statues, Hindu deities)
            # that ARE folk material culture. Trust the museum's own curation.
            if cul.get("vision_vetted") is False and src not in _TRUSTED_MUSEUM_SOURCES:
                # commons_arch on an architectural-monument tradition: trust
                # our own curated category and keep despite vision reject.
                if not (src == "commons_arch" and _is_arch_monument):
                    continue
            # If vision assigned an art_form, prefer it over the rule-based one.
            if cul.get("art_form_vision"):
                cul["art_form"] = cul["art_form_vision"]
            # Apply per-record classifier overrides from
            # data/classifier_overrides.json. "reject" drops the record.
            _override = _classifier_overrides.get(rec.get("id") or "")
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
            global_facets["art_form"][cul.get("art_form") or "unclassified"] += 1
            global_facets["source"][(rec.get("source") or {}).get("museum") or "?"] += 1
            global_facets["country"][country] += 1
            all_objects_count += 1

    print(f"Re-attribution: routed {reroute_stats['routed']} previously _regional records; "
          f"{reroute_stats['unroutable']} could not be attributed and were dropped from the map. "
          f"Junk filter caught {reroute_stats['junk_drop']} pre-existing junk records. "
          f"Classifier overrides: {reroute_stats['classifier_override']} reclassified, "
          f"{reroute_stats['classifier_reject']} rejected.")

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
            # Dedup fingerprint.
            #
            # V&A records almost always lack a title (title=None) and their
            # date_text is a free-string ("before 1875", "ca. 1870", "1850-1900")
            # so a title|year|dims fingerprint doesn't collapse serial fragments.
            # Instead, when the record has no title, use the museum's own
            # accession lot key: (accession-prefix, acquisition-year). Sibling
            # accession numbers from one collection lot — IS.1849-1883 and
            # IS.1850-1883, or 870-1900 and 871-1900 — collapse into one
            # fingerprint and only the highest-scored copy survives dedup.
            #
            # For records WITH a title (Cleveland/Met/Smithsonian) the
            # normalised title + rounded 4-digit year + dims chunk continues
            # to work well.
            import re as _re
            title_norm = (phys.get("title") or "").strip().lower().split(",")[0]
            # Normalise camera-dump serial titles so a photographer's sequence
            # ("48 Madrasah Chor Minor 120.jpg" .. "126.jpg") collapses to one
            # fingerprint. Strip file extension, then any trailing " 123",
            # " 125a", "-123", "_123", "(3)". This runs before the title-less
            # branch so records that become empty after stripping fall through
            # to the accession-lot branch.
            title_norm = _re.sub(r"\.(jpg|jpeg|png|tif|tiff|gif|webp)$", "", title_norm)
            # Strip leading YYYY-MM-DD-HHMMSS or YYYYMMDD prefixes (photo-batch
            # timestamp signatures) — one photographer's session with 20 frames
            # of the same building collapses to one fingerprint.
            title_norm = _re.sub(r"^\d{4}[-_]?\d{2}[-_]?\d{2}[-_\s]?\d{0,6}[a-z]{0,3}[\s\-_]*", "", title_norm)
            # Strip trailing Flickr photo ID in parens like " (29700620670)"
            title_norm = _re.sub(r"\s*\(\d{7,}\)\s*$", "", title_norm)
            # Strip trailing serial suffix (" 12", " 12a", "-12", "_12", "(3)")
            title_norm = _re.sub(r"[\s\-_]*\(?\d{1,4}[a-z]?\)?$", "", title_norm).strip()
            if not title_norm:
                acc = (src.get("accession_number") or "").strip()
                m_prefix = _re.match(r"^([A-Z]+\.?|CIRC\.)", acc)
                prefix = m_prefix.group(1) if m_prefix else ""
                m_year = _re.search(r"-(\d{4})$", acc)
                acc_year = m_year.group(1) if m_year else ""
                fingerprint = f"{src.get('museum','?')}|acc|{prefix}|{acc_year}" if acc else f"{src.get('museum','?')}|noacc|{r.get('id')}"
            else:
                year = phys.get("date_earliest") or phys.get("date_text") or ""
                year_str = str(year)
                m_y = _re.search(r"(\d{4})", year_str)
                year_bucket = m_y.group(1) if m_y else ""
                dim_note = (phys.get("dimensions_note") or "")[:20]
                fingerprint = f"{title_norm}|{year_bucket}|{dim_note}"
            return {
                "id": r.get("id"),
                "title": phys.get("title"),
                "date_text": phys.get("date_text"),
                "art_form": (r.get("cultural") or {}).get("art_form"),
                "tradition": (r.get("cultural") or {}).get("tradition"),
                "pattern_density": (r.get("cultural") or {}).get("pattern_density"),
                "source": src.get("museum"),
                "object_url": src.get("object_url"),
                "image": img_path,
                "place": (r.get("location") or {}).get("made_in_place"),
                # Tile-ordering helpers — not surfaced in the UI directly
                "_score": score,
                "_fp": fingerprint,
            }

        def _dedup_and_rank(items: list[dict]) -> list[dict]:
            """Three passes:
              1. Fingerprint dedup — collapse identical records (title|year|dims
                 for titled records, accession-lot for title-less ones).
              2. Per-(source, tradition) cap at 3 — prevents V&A's title-less
                 suzani serials (12 near-identical tiles) or Cleveland's 6
                 similar wall-hangings from monopolising a tradition group in
                 the UI. The user regroups by tradition, so capping at the
                 (source, tradition) grain is what actually reduces visible
                 duplication.
              3. Interleave sources for the flat gallery view."""
            # Pass 1: fingerprint dedup.
            best_by_fp: dict[str, dict] = {}
            for it in items:
                fp = it.get("_fp") or it.get("id")
                cur = best_by_fp.get(fp)
                if cur is None or (it.get("_score") or 0) > (cur.get("_score") or 0):
                    best_by_fp[fp] = it
            deduped = sorted(best_by_fp.values(), key=lambda x: -(x.get("_score") or 0))

            # Pass 2: cap per (source, tradition). Titled records get a
            # generous cap because titles help distinguish visually similar
            # objects. Title-less records (V&A serials, mostly) get a strict
            # cap because tile labels collapse to just the tradition and 5+
            # of them look like duplicate cards.
            TITLED_CAP = 8
            TITLELESS_CAP = 3
            titled_kept: dict[tuple, int] = defaultdict(int)
            titleless_kept: dict[tuple, int] = defaultdict(int)
            capped: list[dict] = []
            for it in deduped:
                key = (it.get("source") or "?", (it.get("tradition") or "").lower())
                if it.get("title"):
                    if titled_kept[key] >= TITLED_CAP:
                        continue
                    titled_kept[key] += 1
                else:
                    if titleless_kept[key] >= TITLELESS_CAP:
                        continue
                    titleless_kept[key] += 1
                capped.append(it)

            # Pass 3: interleave by TRADITION so a top-N view naturally shows
            # one representative per sub-category (Bibi-Khanym once, Chor Minor
            # once, Registan once, ...) before circling back for a second pass.
            # The frontend renders a flat gallery per art_form, so this
            # ordering is what the user sees first.
            by_trad: dict[str, list[dict]] = defaultdict(list)
            for it in capped:
                by_trad[(it.get("tradition") or "").lower() or "?"].append(it)
            interleaved: list[dict] = []
            while any(by_trad.values()):
                for t in list(by_trad.keys()):
                    if by_trad[t]:
                        interleaved.append(by_trad[t].pop(0))
            return interleaved
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
            "object_count": real_object_count + len(art_form_buckets.get("photo", [])),
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
    (out_root / "globe.json").write_text(
        json.dumps({"points": globe_points}, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Wrote data/index.json  ({all_objects_count} objects, {len(globe_points)} globe points)")
    print(f"Wrote {len(eth_meta)} ethnicity shards, {sum(len(v) for v in objects_by_eth.values())} object shards")


if __name__ == "__main__":
    build()
