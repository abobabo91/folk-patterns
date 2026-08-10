"""Re-run classify.py against every record in the library, updating the
`cultural.art_form` and `cultural.pattern_density` fields in place. Useful
after extending classify.py with new vocabulary (multilingual terms, new
regions).

Reclassifies from each record's own `physical.classification`, `title`, and
raw material/technique info — no re-scraping needed."""
from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import LIBRARY_DIR
from folk_patterns.classify import classify


def _rec_to_classify_input(rec: dict) -> dict:
    """Turn a canonical record into the flat dict classify() expects.

    Also pulls extra raw fields for Europeana records — search-response items
    have dcDescription / dcSubject that aren't stored in `physical` but often
    contain the object-type keyword needed for classification."""
    phys = rec.get("physical") or {}
    raw = rec.get("raw") or {}
    extras = []
    if (rec.get("source") or {}).get("museum") == "europeana":
        for k in ("dcDescription", "dcSubject", "dcType", "dcFormat"):
            v = raw.get(k)
            if isinstance(v, list):
                extras.extend(str(x) for x in v if isinstance(x, str))
            elif isinstance(v, str):
                extras.append(v)
    return {
        "classification": phys.get("classification") or "",
        "object_type": phys.get("classification") or "",
        "medium": phys.get("medium_raw") or "",
        "material_technique": " ".join((phys.get("materials") or []) + (phys.get("techniques") or [])),
        "title": (phys.get("title") or "") + " " + " ".join(extras),
        "objectName": phys.get("title") or "",
        "summary": phys.get("summary") or "",
        "description": phys.get("physical_description") or "",
    }


def main() -> None:
    """Rewrite art_form in place. Records whose art_form changes must also
    move on disk — otherwise `library/<region>/<country>/<eth>/<art_form>/`
    and `cultural.art_form` disagree, producing folder-vs-record drift the
    site's build_index has to reconcile."""
    import shutil, os
    changed = 0
    moved = 0
    total = 0
    transitions: Counter = Counter()
    # Group updates by (source_meta_path -> list of (idx, record, new_dest_dir))
    per_file: dict = {}
    # Sources whose art_form is derived from external signals (seed category
    # in commons_arch's case, unit_code in smithsonian, etc), not the record's
    # own title/medium fields. classify() would blindly return "unclassified"
    # for their filename-only titles and destroy legitimate art_form values.
    SKIP_SOURCES = {"commons_arch"}
    for meta_path in sorted(LIBRARY_DIR.glob("*/*/*/*/*/metadata.json")):
        recs = json.loads(meta_path.read_text(encoding="utf-8"))
        for i, r in enumerate(recs):
            total += 1
            if (r.get("source") or {}).get("museum") in SKIP_SOURCES:
                continue
            cul = r.setdefault("cultural", {})
            old = cul.get("art_form")
            cf = classify(_rec_to_classify_input(r))
            new = cf["art_form"]
            if old != new:
                transitions[(old, new)] += 1
                cul["art_form"] = new
                cul["pattern_density"] = cf["pattern_density"]
                changed += 1
                per_file.setdefault(str(meta_path), []).append((i, r, old, new))
        # Persist updated art_form in-place first
        meta_path.write_text(json.dumps(recs, indent=2, ensure_ascii=False), encoding="utf-8")
    # Now move each changed record to its new folder + rewrite its images'
    # local_path. Idempotent — safe to re-run.
    for meta_path_str, moves in per_file.items():
        meta_path = Path(meta_path_str)
        recs = json.loads(meta_path.read_text(encoding="utf-8"))
        keep = [r for i, r in enumerate(recs) if not any(mi == i for mi, _, _, _ in moves)]
        for i, rec, old_af, new_af in moves:
            # Build new dest: swap art_form segment.
            parts = list(meta_path.parts)
            try:
                lib_idx = parts.index("library")
            except ValueError:
                continue
            # parts: library/<region>/<country>/<eth>/<art_form>/<tradition>/metadata.json
            parts[lib_idx + 4] = new_af
            new_dir = Path(*parts[:-1])
            new_dir.mkdir(parents=True, exist_ok=True)
            (new_dir / "images").mkdir(exist_ok=True)
            # Move images + rewrite local_path
            for img in rec.get("images") or []:
                lp = img.get("local_path")
                if not lp:
                    continue
                old_full = Path(lp)
                if not old_full.exists():
                    # try relative to repo root (already relative)
                    old_full = Path.cwd() / lp
                if old_full.exists():
                    new_full = new_dir / "images" / old_full.name
                    if not new_full.exists():
                        shutil.move(str(old_full), str(new_full))
                    # Update local_path relative to library/
                    img["local_path"] = str(new_full.relative_to(Path.cwd()))
            # Append rec to new metadata.json
            new_meta = new_dir / "metadata.json"
            new_arr = json.loads(new_meta.read_text(encoding="utf-8")) if new_meta.exists() else []
            new_arr = [x for x in new_arr if x.get("id") != rec.get("id")]
            new_arr.append(rec)
            new_meta.write_text(json.dumps(new_arr, indent=2, ensure_ascii=False), encoding="utf-8")
            moved += 1
        # Rewrite source meta_path without the moved records
        meta_path.write_text(json.dumps(keep, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Reclassified {changed} of {total} records, moved {moved} to new folders")
    print("Top transitions (old -> new):")
    for (o, n), c in transitions.most_common(20):
        print(f"  {c:>4}  {o!r} -> {n!r}")


if __name__ == "__main__":
    main()
