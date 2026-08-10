"""Per-ethnicity completeness sweep — one-off diagnostic, not part of the pipeline."""
from __future__ import annotations
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
CONTENT = ROOT / "content"
SEED = ROOT / "data" / "seed"

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Load seed to know what "should" exist per ethnicity: declared traditions,
# and per-museum source_queries hints.
seed_map: dict[tuple[str, str], dict] = {}  # (region, ethnicity_slug) -> entry
region_files = {
    "central-asia": "central_asia.json",
    "middle-east-north-africa": "mena.json",
    "southeast-asia": "southeast_asia.json",
    "sub-saharan-africa": "sub_saharan_africa.json",
}
for region, fname in region_files.items():
    p = SEED / fname
    if not p.exists():
        continue
    data = json.loads(p.read_text(encoding="utf-8"))
    for country in data.get("countries", []):
        for eth in country.get("ethnicities", []):
            slug = eth.get("slug") or eth.get("name", "").lower().replace(" ", "-")
            seed_map[(region, slug)] = {
                "name": eth.get("name"),
                "country": country.get("name"),
                "traditions": eth.get("traditions", []),
                "source_queries": eth.get("source_queries", {}),
                "homeland": eth.get("homeland"),
            }

# Walk library
rows = []
for region_dir in sorted(LIB.iterdir()):
    if not region_dir.is_dir():
        continue
    region = region_dir.name
    for country_dir in sorted(region_dir.iterdir()):
        if not country_dir.is_dir():
            continue
        country = country_dir.name
        for eth_dir in sorted(country_dir.iterdir()):
            if not eth_dir.is_dir():
                continue
            eth = eth_dir.name

            # Enumerate art_form / tradition / metadata files
            art_forms_seen = set()
            art_forms_with_records = set()
            traditions_seen = set()
            traditions_with_records = set()
            museum_counts: Counter = Counter()
            record_count = 0
            image_count = 0
            empty_meta_dirs = 0
            populated_meta_dirs = 0

            for meta_file in eth_dir.rglob("metadata.json"):
                rel_parts = meta_file.relative_to(eth_dir).parts
                # expected shape: <art_form>/<tradition>/metadata.json
                if len(rel_parts) < 3:
                    continue
                art_form = rel_parts[0]
                tradition = rel_parts[1]
                art_forms_seen.add(art_form)
                traditions_seen.add(tradition)
                try:
                    records = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    records = []
                if not records:
                    empty_meta_dirs += 1
                    continue
                populated_meta_dirs += 1
                art_forms_with_records.add(art_form)
                traditions_with_records.add(tradition)
                record_count += len(records)
                for r in records:
                    src = (r.get("source") or {}).get("museum", "unknown")
                    museum_counts[src] += 1
                    for img in r.get("images") or []:
                        if img.get("local_path"):
                            image_count += 1

            # Fallback: actual image files on disk (some may be there without meta)
            disk_images = sum(
                1 for p in eth_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in IMG_EXTS
            )

            # Writeup?
            writeup_path = CONTENT / region / f"{country}__{eth}.md"
            has_writeup = writeup_path.exists()

            # Seed match
            seed_entry = seed_map.get((region, eth))
            seed_traditions_declared = len(seed_entry["traditions"]) if seed_entry else 0
            seed_declared = seed_entry is not None

            rows.append({
                "region": region,
                "country": country,
                "ethnicity": eth,
                "seed_declared": seed_declared,
                "has_writeup": has_writeup,
                "disk_images": disk_images,
                "records": record_count,
                "art_forms_with_records": len(art_forms_with_records),
                "art_forms_seen": len(art_forms_seen),
                "traditions_with_records": len(traditions_with_records),
                "traditions_declared_in_seed": seed_traditions_declared,
                "museums_covered": len(museum_counts),
                "museums": ",".join(sorted(museum_counts)) or "-",
                "empty_meta_dirs": empty_meta_dirs,
                "populated_meta_dirs": populated_meta_dirs,
            })

# Classify
def status(r: dict) -> str:
    if r["disk_images"] == 0:
        return "EMPTY"
    if r["disk_images"] < 15:
        return "BARELY"
    if r["disk_images"] < 40 or r["art_forms_with_records"] <= 1 or r["museums_covered"] <= 1:
        return "THIN"
    if r["art_forms_with_records"] >= 4 and r["museums_covered"] >= 3 and r["disk_images"] >= 60:
        return "COMPLETE"
    return "OK"

for r in rows:
    r["status"] = status(r)

# Print buckets
by_status: dict[str, list] = defaultdict(list)
for r in rows:
    by_status[r["status"]].append(r)

order = ["COMPLETE", "OK", "THIN", "BARELY", "EMPTY"]
print(f"{'STATUS':<9} {'IMG':>5} {'REC':>4} {'AF':>3} {'MU':>3} {'WU':>3}  ETHNICITY")
print("-" * 90)
totals = Counter()
for st in order:
    for r in sorted(by_status[st], key=lambda x: -x["disk_images"]):
        wu = "y" if r["has_writeup"] else "-"
        totals[st] += 1
        print(f"{st:<9} {r['disk_images']:>5} {r['records']:>4} {r['art_forms_with_records']:>3} {r['museums_covered']:>3} {wu:>3}  {r['region']}/{r['country']}/{r['ethnicity']}  [{r['museums']}]")
    print()

print("=" * 60)
for st in order:
    print(f"{st:<10} {totals[st]:>3}")
print(f"{'TOTAL':<10} {sum(totals.values()):>3}")

# Missing museums per ethnicity — an ethnicity that's THIN and only covers Met but seed
# declares source_queries for other museums is a strong signal we skipped a museum.
print()
print("=== Ethnicities where seed declares source_queries the scraper never touched ===")
for r in rows:
    if not r["seed_declared"]:
        continue
    key = (r["region"], r["ethnicity"])
    seed_entry = seed_map.get(key)
    if not seed_entry:
        continue
    declared_sq = set(seed_entry.get("source_queries", {}).keys())
    covered = set(r["museums"].split(",")) if r["museums"] != "-" else set()
    missing = declared_sq - covered
    if missing:
        print(f"  {r['region']}/{r['ethnicity']}: seed→{sorted(declared_sq)} covered→{sorted(covered) or '[]'} MISSING={sorted(missing)}  ({r['disk_images']} imgs)")

# Ethnicities in library but not in seed (drift)
print()
print("=== Library dirs with no matching seed entry ===")
for r in rows:
    if not r["seed_declared"]:
        print(f"  {r['region']}/{r['country']}/{r['ethnicity']}  ({r['disk_images']} imgs, writeup={r['has_writeup']})")

# Seed entries with no library dir
print()
print("=== Seed entries with no library directory ===")
seen_keys = {(r["region"], r["ethnicity"]) for r in rows}
for (region, slug), entry in seed_map.items():
    if (region, slug) not in seen_keys:
        print(f"  {region}/{slug}  ({entry['name']}, {entry['country']})")
