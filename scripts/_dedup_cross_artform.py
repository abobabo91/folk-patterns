"""Cross-art_form dedup within an ethnicity.

If the same object_id appears in multiple art_form buckets for the same
(region, country, ethnicity), keep the classification with the highest
priority and delete the others (both metadata and image files).

Priority (highest first): specific art_forms → unclassified → photo.
"""
from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"

ART_FORM_PRIORITY = [
    "textile", "garment", "jewelry", "metalwork", "ceramic",
    "sculpture", "painting-mss", "architectural", "household",
    "unclassified", "photo",
]

def _priority(af: str) -> int:
    try: return ART_FORM_PRIORITY.index(af)
    except ValueError: return 999

# Collect all (ethnicity_slug, object_id) -> [(priority, meta_path, record_index)]
by_key = defaultdict(list)
for meta in LIB.rglob("metadata.json"):
    parts = meta.relative_to(LIB).parts
    if len(parts) < 5: continue
    region, country, ethn, art_form, tradition = parts[0], parts[1], parts[2], parts[3], parts[4]
    try: recs = json.loads(meta.read_text(encoding="utf-8"))
    except: recs = []
    for i, rec in enumerate(recs):
        rid = rec.get("id")
        if not rid: continue
        key = (region, country, ethn, rid)
        by_key[key].append((_priority(art_form), art_form, meta, i))

removed_recs = 0
deleted_imgs = 0
touched_files = set()

for key, entries in by_key.items():
    if len(entries) < 2: continue
    # Sort by priority (best = lowest number)
    entries.sort(key=lambda x: x[0])
    # Keep [0], remove the rest
    keep = entries[0]
    for _, art_form, meta, idx in entries[1:]:
        # Load the file (may have been modified during this loop)
        try: recs = json.loads(meta.read_text(encoding="utf-8"))
        except: continue
        # Find matching record by id (indexes shift if we've removed already)
        matching_indices = [j for j, r in enumerate(recs) if r.get("id") == key[3]]
        for j in matching_indices:
            rec = recs[j]
            # Delete image files
            for img in rec.get("images") or []:
                lp = img.get("local_path") or ""
                fname = Path(lp).name
                if fname:
                    img_path = meta.parent / "images" / fname
                    if img_path.exists():
                        img_path.unlink()
                        deleted_imgs += 1
        # Remove records with this id from the file
        new_recs = [r for r in recs if r.get("id") != key[3]]
        if len(new_recs) != len(recs):
            removed_recs += (len(recs) - len(new_recs))
            meta.write_text(json.dumps(new_recs, ensure_ascii=False, indent=2), encoding="utf-8")
            touched_files.add(meta)

# Clean up any now-empty images/ dirs
for meta in touched_files:
    imgs_dir = meta.parent / "images"
    if imgs_dir.exists() and not any(imgs_dir.iterdir()):
        imgs_dir.rmdir()

print(f"Cross-art_form duplicate records removed: {removed_recs}")
print(f"Duplicate image files deleted: {deleted_imgs}")
print(f"Metadata files modified: {len(touched_files)}")
