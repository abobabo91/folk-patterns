"""Reorganize an existing library/<region>/ from
    <country>/<ethnicity>/<tradition>/{images/, metadata.json}
into
    <country>/<ethnicity>/<pattern-type>/<tradition>/{images/, metadata.json}
    plus a shared <_regional>/<pattern-type>/ bucket for objects duplicated
    across countries (same source+object_id in multiple country trees).

Uses folk_patterns.classify.classify_pattern_type on each record.

Also deduplicates cross-country: if the same (source, object_id) appears in
more than one country, we keep it in the first country only when it has a
country-specific attribution in the metadata; otherwise move it to _regional/.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import LIBRARY_DIR
from folk_patterns.classify import classify_pattern_type


def _iter_metadata(region_dir: Path):
    """Yield (metadata_path, country, ethnicity, tradition, records)."""
    for country_dir in sorted(region_dir.iterdir()):
        if not country_dir.is_dir() or country_dir.name.startswith("_"):
            continue
        for eth_dir in sorted(country_dir.iterdir()):
            if not eth_dir.is_dir():
                continue
            for trad_dir in sorted(eth_dir.iterdir()):
                if not trad_dir.is_dir():
                    continue
                mp = trad_dir / "metadata.json"
                if not mp.exists():
                    continue
                records = json.loads(mp.read_text(encoding="utf-8"))
                yield mp, country_dir.name, eth_dir.name, trad_dir.name, records


def _is_country_specific(rec: dict, country: str) -> bool:
    """Does this record actually claim country-specific attribution?"""
    fields = " ".join(
        str(rec.get(k, "") or "").lower()
        for k in ("culture", "country", "region", "subregion", "place",
                 "geographyType", "query_ethnicity", "query_country")
    )
    country_lower = country.lower()
    # Strip any suffixes like "(xinjiang)" from folder-slug country names.
    if country_lower not in fields:
        # try without special chars
        cleaned = country_lower.replace("-", " ").replace("(", "").replace(")", "").strip()
        return any(bit in fields for bit in cleaned.split() if len(bit) >= 4)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region", help="Region slug — subfolder under library/")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    region_dir = LIBRARY_DIR / args.region
    if not region_dir.exists():
        print(f"! no library/{args.region}")
        sys.exit(1)

    # PASS 1: collect all records and detect cross-country duplicates.
    key_to_locations: dict[tuple[str, str], list[tuple[str, str, str, dict]]] = defaultdict(list)
    for mp, country, ethnicity, tradition, records in _iter_metadata(region_dir):
        for r in records:
            key = (r["source"], r["object_id"])
            key_to_locations[key].append((country, ethnicity, tradition, r))

    n_cross = sum(1 for locs in key_to_locations.values() if len({(l[0]) for l in locs}) > 1)
    print(f"Total unique objects: {len(key_to_locations)}. Cross-country duplicates: {n_cross}.")

    # Decide the canonical placement for each object.
    # If exactly one country-specific attribution → that country
    # If none specific → _regional
    # If multiple specific → keep in the first
    placement: dict[tuple[str, str], tuple[str, str, str, dict]] = {}
    for key, locs in key_to_locations.items():
        specific = [(c, e, t, r) for c, e, t, r in locs if _is_country_specific(r, c)]
        if len(specific) >= 1:
            placement[key] = specific[0]
        else:
            # No country really claims it. Use the first ethnicity as label.
            _, e, t, r = locs[0]
            placement[key] = ("_regional", e, t, r)

    # PASS 2: apply classifier and figure out new path.
    # New path: <country>/<ethnicity>/<pattern_type>/<tradition>/images/<file>
    moves: list[tuple[Path, Path, dict]] = []  # (old_img, new_img, record)
    old_metadata_files: set[Path] = set()
    new_records_by_dir: dict[Path, list[dict]] = defaultdict(list)

    for key, (country, ethnicity, tradition, rec) in placement.items():
        pattern_type = classify_pattern_type(rec)
        new_dir = region_dir / country / ethnicity / pattern_type / tradition
        new_img = new_dir / "images" / Path(rec["image_path"]).name

        # Old image path is stored in rec["image_path"] as region-relative.
        old_img = LIBRARY_DIR / Path(rec["image_path"])
        if not old_img.exists():
            # image was already moved in an earlier run — skip
            continue
        moves.append((old_img, new_img, rec))
        rec2 = dict(rec)
        # Rewrite image_path so it stays valid post-move.
        rec2["image_path"] = str(new_img.relative_to(LIBRARY_DIR))
        rec2["pattern_type"] = pattern_type
        new_records_by_dir[new_dir].append(rec2)
        old_metadata_files.add(LIBRARY_DIR / Path(rec["image_path"]).parent.parent / "metadata.json")

    print(f"Moves planned: {len(moves)}")
    from collections import Counter
    type_counter = Counter(r["pattern_type"] for recs in new_records_by_dir.values() for r in recs)
    print("Pattern type distribution:")
    for k, v in type_counter.most_common():
        print(f"  {v:4d}  {k}")

    if args.dry_run:
        return

    # PASS 3: execute moves. Move image files, then remove old empty dirs, then
    # write new metadata.json per new_dir.
    for old_img, new_img, _rec in moves:
        new_img.parent.mkdir(parents=True, exist_ok=True)
        if new_img.exists():
            old_img.unlink(missing_ok=True)
        else:
            shutil.move(str(old_img), str(new_img))

    for new_dir, recs in new_records_by_dir.items():
        (new_dir / "metadata.json").write_text(
            json.dumps(recs, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # Clean up: remove old metadata.json files, then any empty dirs.
    for mp in old_metadata_files:
        if mp.exists():
            mp.unlink()
    for country_dir in region_dir.iterdir():
        if not country_dir.is_dir():
            continue
        for eth_dir in list(country_dir.iterdir()) if country_dir.is_dir() else []:
            if not eth_dir.is_dir():
                continue
            for sub in list(eth_dir.iterdir()) if eth_dir.is_dir() else []:
                # Any subdir that isn't one of our new pattern-type dirs and is
                # empty (or holds only an empty images/) should be removed.
                if not sub.is_dir():
                    continue
                if sub.name in {"textile-pattern", "garment-pattern", "tile-pattern", "wallpaper-pattern", "not-pattern", "unclassified"}:
                    continue
                # Remove if empty
                images = sub / "images"
                if images.exists() and not any(images.iterdir()):
                    shutil.rmtree(sub)
                elif not any(sub.iterdir()):
                    shutil.rmtree(sub)

    print("Done.")


if __name__ == "__main__":
    main()
