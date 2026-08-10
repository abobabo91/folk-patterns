"""Flag <30KB images as low_quality=True so the site build can dim/hide them.

Not a delete — a metadata annotation. The image still exists on disk in case
we later find a way to refetch full-res, but build_index.py can skip these
when picking hero images for the ethnicity page.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"

flagged = 0
files_touched = 0

for meta in LIB.rglob("metadata.json"):
    try: recs = json.loads(meta.read_text(encoding="utf-8"))
    except: continue
    changed = False
    for rec in recs:
        for img in rec.get("images") or []:
            b = img.get("bytes")
            if b is not None and b < 30000 and not img.get("low_quality"):
                img["low_quality"] = True
                flagged += 1
                changed = True
    if changed:
        meta.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
        files_touched += 1

print(f"Images flagged low_quality=True: {flagged}")
print(f"Metadata files modified: {files_touched}")
