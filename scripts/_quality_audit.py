"""Quality audit — flag records/images likely to be wrong or degrade the atlas.

Checks:
  1. Attribution drift: date range that pre-dates the ethnicity's existence
     (e.g., Kushan-era piece tagged Afghan Turkmen)
  2. Orphan images: image files with no corresponding metadata record
  3. Orphan records: metadata records referencing an image file that doesn't exist
  4. Duplicate records: same object_id filed under >1 (region, country, ethnicity)
  5. Tiny/broken images: files <30KB (likely thumbnails or failed downloads)
  6. Metadata completeness: records with all-null physical fields (title, date, medium)
  7. Bad culture-string bleed: culture-string mentions a group NOT in the ethnicity bucket
     (e.g. record with culture=['Chinese'] filed under Lao)
"""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"

# Rough origin windows — after which the ethnonym became a stable identity.
# Records dated BEFORE the "not-before" year should not be attributed to
# these ethnicities (they belong to predecessor cultures).
NOT_BEFORE = {
    "Afghan Turkmen": 1200,   # Oghuz Turkmen only in region ~1200 CE
    "Turkmen": 1000,
    "Uzbek": 1400,            # Uzbek ethnonym stabilizes with Shaybanids
    "Kazakh": 1450,           # Kazakh Khanate 1465
    "Kyrgyz": 800,            # Yenisey Kyrgyz earlier but modern Kyrgyz later
    "Karakalpak": 1500,
    "Uyghur": 800,            # modern Uyghur political identity newer; culture older
    "Bamar": 1000,
    "Filipino": 1500,
    "Vietnamese": 800,
    "Somali": 900,
    "Yoruba": 800,
    "Bukharan Jew": 500,
    "Hazara": 1300,
    "Sundanese": 500,
    "Javanese": 500,
    "Balinese": 800,
}

def _to_int(x):
    try: return int(x)
    except: return None

rows_drift = []
rows_orphan_img = []
rows_orphan_rec = []
duplicates = defaultdict(list)   # object_id -> [paths]
rows_broken_img = []
rows_bare_meta = []
rows_culture_bleed = []
rows_multi_ethn_same_id = defaultdict(set)

all_metas = list(LIB.rglob("metadata.json"))
total_recs = 0
total_imgs_referenced = 0
total_imgs_on_disk = 0

for meta in all_metas:
    parts = meta.relative_to(LIB).parts
    if len(parts) < 5:
        continue
    region, country, ethn_slug, art_form, tradition = parts[0], parts[1], parts[2], parts[3], parts[4]
    try:
        records = json.loads(meta.read_text(encoding="utf-8"))
    except:
        records = []
    imgs_dir = meta.parent / "images"
    disk_images = set()
    if imgs_dir.exists():
        disk_images = {p.name for p in imgs_dir.iterdir() if p.is_file()}
    total_imgs_on_disk += len(disk_images)
    referenced_images = set()

    for rec in records:
        total_recs += 1
        rec_id = rec.get("id") or "?"
        cul = rec.get("cultural") or {}
        eth_name = cul.get("ethnicity") or "?"

        # (4) duplicate detection
        rows_multi_ethn_same_id[rec_id].add((region, country, ethn_slug))
        duplicates[rec_id].append(f"{region}/{country}/{ethn_slug}")

        # (1) attribution drift by date
        phys = rec.get("physical") or {}
        d_latest = _to_int(phys.get("date_latest"))
        d_earliest = _to_int(phys.get("date_earliest"))
        nb = NOT_BEFORE.get(eth_name)
        if nb and d_latest is not None and d_latest < nb:
            rows_drift.append({
                "record": rec_id,
                "eth": eth_name,
                "date_range": f"{d_earliest}-{d_latest}",
                "not_before": nb,
                "title": (phys.get("title") or "")[:80],
                "path": str(meta.relative_to(LIB).parent),
            })

        # (6) bare metadata (all key physical fields null/empty)
        title = phys.get("title")
        medium = phys.get("medium_raw")
        date = phys.get("date_text")
        if not any([title, medium, date, phys.get("summary")]):
            rows_bare_meta.append({
                "id": rec_id, "path": str(meta.relative_to(LIB).parent),
            })

        # (7) culture bleed
        raw = rec.get("raw") or {}
        raw_culture = ""
        if isinstance(raw.get("culture"), list):
            raw_culture = ",".join(str(x) for x in raw["culture"]).lower()
        elif isinstance(raw.get("culture"), str):
            raw_culture = raw["culture"].lower()
        if raw_culture:
            problematic = {
                "Chin": ["chinese"],
                "Lao": ["liao", "chinese"],
                "Kinh": ["chinese"],
                "Cham": ["chinese"],
                "Bamar": ["thai", "chinese"],
                "Thai": ["chinese", "burmese"],
            }
            for token in (problematic.get(eth_name) or []):
                # avoid Chin ⊂ chinese false alarm
                import re
                if re.search(r"\b" + re.escape(token) + r"\b", raw_culture):
                    # But ensure the ethnicity token isn't ALSO in the string
                    if eth_name.lower() not in raw_culture:
                        rows_culture_bleed.append({
                            "id": rec_id, "eth": eth_name,
                            "culture": raw_culture[:100],
                            "path": str(meta.relative_to(LIB).parent),
                        })
                        break

        # image checks
        for img in rec.get("images") or []:
            total_imgs_referenced += 1
            lp = img.get("local_path") or ""
            fname = Path(lp).name if lp else ""
            if fname:
                referenced_images.add(fname)
            bytes_ = img.get("bytes")
            if bytes_ is not None and bytes_ < 30000:
                rows_broken_img.append({
                    "id": rec_id, "bytes": bytes_,
                    "path": str(meta.relative_to(LIB).parent) + "/images/" + fname,
                })

    # (2) orphan images = files on disk not referenced by any record
    orphans = disk_images - referenced_images
    for o in orphans:
        rows_orphan_img.append({"path": str(meta.relative_to(LIB).parent) + "/images/" + o})

    # (3) orphan records = referenced files not on disk
    missing = referenced_images - disk_images
    for m in missing:
        rows_orphan_rec.append({"path": str(meta.relative_to(LIB).parent) + "/images/" + m})

# Duplicate summary: object_ids that appear in >1 (region, country, ethnicity)
real_dups = {k: v for k, v in rows_multi_ethn_same_id.items() if len(v) > 1}
same_bucket_dups = {k: v for k, v in duplicates.items() if len(v) > 1 and k not in real_dups}
# same_bucket_dups: id filed twice in same bucket (e.g., cle_XXX in same eth twice)

# Report
print(f"\n{'='*60}\n QUALITY AUDIT\n{'='*60}\n")
print(f"Records:                {total_recs}")
print(f"Image refs in records:  {total_imgs_referenced}")
print(f"Images on disk:         {total_imgs_on_disk}\n")

print(f"1. ATTRIBUTION DRIFT (record dated before ethnicity existed): {len(rows_drift)}")
for r in rows_drift[:12]:
    print(f"   {r['eth']:20s} dated {r['date_range']:12s} (not-before {r['not_before']})  {r['title']}")
if len(rows_drift) > 12: print(f"   ... and {len(rows_drift)-12} more")

print(f"\n2. ORPHAN IMAGES (file on disk, no metadata record): {len(rows_orphan_img)}")
for r in rows_orphan_img[:10]:
    print(f"   {r['path']}")
if len(rows_orphan_img) > 10: print(f"   ... and {len(rows_orphan_img)-10} more")

print(f"\n3. ORPHAN RECORDS (metadata references missing image): {len(rows_orphan_rec)}")
for r in rows_orphan_rec[:10]:
    print(f"   {r['path']}")
if len(rows_orphan_rec) > 10: print(f"   ... and {len(rows_orphan_rec)-10} more")

print(f"\n4. CROSS-BUCKET DUPLICATES (same object_id in >1 ethnicity): {len(real_dups)}")
for k, v in list(real_dups.items())[:10]:
    print(f"   {k}: {', '.join(f'{r}/{c}/{e}' for r,c,e in v)}")

print(f"\n5. SAME-BUCKET DUPLICATES (id appears twice in same bucket): {len(same_bucket_dups)}")

print(f"\n6. BROKEN/TINY IMAGES (<30KB, likely thumbnail): {len(rows_broken_img)}")
for r in rows_broken_img[:5]:
    print(f"   {r['bytes']:6d}B  {r['path']}")

print(f"\n7. BARE METADATA (no title/date/medium/summary): {len(rows_bare_meta)}")
for r in rows_bare_meta[:5]:
    print(f"   {r['id']}  {r['path']}")

print(f"\n8. CULTURE-STRING BLEED (suspicious raw culture): {len(rows_culture_bleed)}")
for r in rows_culture_bleed[:8]:
    print(f"   {r['eth']:12s} culture={r['culture'][:80]}  {r['path']}")
