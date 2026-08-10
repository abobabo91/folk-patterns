"""One-off audit: find records whose cultural.art_form disagrees with the
folder they're stored in. Prints top mismatches + samples."""
import json
import glob
import os
import sys
import io
import collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

mismatches = collections.Counter()
samples = []
for f in glob.glob("library/**/metadata.json", recursive=True):
    parts = f.split(os.sep)
    try:
        li = parts.index("library")
    except ValueError:
        continue
    if len(parts) < li + 6:
        continue
    folder_af = parts[li + 4]
    try:
        arr = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    for r in arr:
        rec_af = r["cultural"]["art_form"]
        if rec_af and rec_af != folder_af:
            mismatches[(folder_af, rec_af)] += 1
            samples.append((f, r["id"], r["physical"]["title"], rec_af, folder_af))

print(f"total mismatches: {sum(mismatches.values())}")
for (fold, rec), n in mismatches.most_common(20):
    print(f"  folder={fold!r} record={rec!r}: {n}")
print()
for s in samples[:10]:
    print(f"  {s[0]}")
    print(f"    id={s[1]} folder={s[4]!r} record={s[3]!r} title={s[2]!r}")
