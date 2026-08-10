"""Quality fix pass:
  1. Same-bucket dedup: within one metadata.json, keep first occurrence of each id
  2. Orphan images: delete files not referenced by any record
  3. Attribution drift: delete records whose date range predates ethnicity existence
     (Kushan/Sogdian/Buddha records mis-attributed to modern ethnonym)
  4. Bare metadata: leave alone (only 1 case)

Cross-bucket dedup is NOT done here — it needs a canonical-owner policy first.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"

NOT_BEFORE = {
    "Afghan Turkmen": 1200, "Turkmen": 1000, "Uzbek": 1400, "Kazakh": 1450,
    "Kyrgyz": 800, "Karakalpak": 1500, "Uyghur": 800, "Bamar": 1000,
    "Filipino": 1500, "Vietnamese": 800, "Somali": 900, "Yoruba": 800,
    "Bukharan Jew": 500, "Hazara": 1300, "Sundanese": 500, "Javanese": 500,
    "Balinese": 800,
}

def _to_int(x):
    try: return int(x)
    except: return None

dedup_removed = 0
orphan_deleted = 0
drift_removed = 0
files_touched = 0

for meta in LIB.rglob("metadata.json"):
    try:
        recs = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        recs = []
    original_count = len(recs)

    # (1) same-bucket dedup by id
    seen = set()
    deduped = []
    for r in recs:
        rid = r.get("id")
        if rid and rid in seen:
            dedup_removed += 1
            continue
        if rid:
            seen.add(rid)
        deduped.append(r)

    # (3) drift removal
    surviving = []
    for r in deduped:
        cul = r.get("cultural") or {}
        eth = cul.get("ethnicity") or ""
        nb = NOT_BEFORE.get(eth)
        d_latest = _to_int((r.get("physical") or {}).get("date_latest"))
        if nb and d_latest is not None and d_latest < nb:
            drift_removed += 1
            # Also delete the associated image file(s)
            for img in r.get("images") or []:
                lp = img.get("local_path") or ""
                fname = Path(lp).name
                if fname:
                    img_path = meta.parent / "images" / fname
                    if img_path.exists():
                        img_path.unlink()
            continue
        surviving.append(r)

    if len(surviving) != original_count:
        meta.write_text(json.dumps(surviving, ensure_ascii=False, indent=2), encoding="utf-8")
        files_touched += 1

    # (2) orphan image cleanup for THIS bucket
    referenced = set()
    for r in surviving:
        for img in r.get("images") or []:
            fname = Path(img.get("local_path") or "").name
            if fname:
                referenced.add(fname)
    imgs_dir = meta.parent / "images"
    if imgs_dir.exists():
        for f in imgs_dir.iterdir():
            if f.is_file() and f.name not in referenced:
                f.unlink()
                orphan_deleted += 1
        # remove empty images dir
        if not any(imgs_dir.iterdir()):
            imgs_dir.rmdir()

print(f"Same-bucket duplicates removed: {dedup_removed}")
print(f"Attribution-drift records removed: {drift_removed}")
print(f"Orphan image files deleted: {orphan_deleted}")
print(f"Metadata files touched: {files_touched}")
