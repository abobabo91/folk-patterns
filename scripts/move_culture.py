"""Move a culture to another site region, or to another region's seed file.

A culture's region is part of its key (`east-asia__russia__bashkir`) and of
every path that holds it: the library folder (and so its R2 image keys), the
writeups and media sidecars under content/, the shards and territories under
territory caches under data/, and its seed entry. This renames all of them in one go.

    python scripts/move_culture.py east-asia__russia__bashkir europe            # dry run
    python scripts/move_culture.py east-asia__russia__bashkir europe --commit
    python scripts/move_culture.py middle-east-north-africa__armenia__armenian caucasus --commit

Afterwards: `python scripts/upload_to_r2.py --commit` (uploads the images under
their new keys; the old keys stay in the bucket), `unvetted.py build`,
`build_index.py`. A stub culture has no library folder or seed entry; for
stubs the region comes from `unvetted.py` `_site_region`, so moving them is a
rule change there plus this script for the content files.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "data" / "seed"


def _seed_file(region: str) -> Path | None:
    for p in SEED.glob("*.json"):
        if json.loads(p.read_text(encoding="utf-8")).get("region") == region:
            return p
    return None


def _slug_name(seed_country: dict, eth_slug: str) -> dict | None:
    from slugify import slugify   # the same python-slugify build_index.py keys with
    return next((e for e in seed_country["ethnicities"] if slugify(e["name"]) == eth_slug), None)


def move(key: str, new_region: str, commit: bool) -> None:
    old_region, country_slug, eth_slug = key.split("__")
    new_key = f"{new_region}__{country_slug}__{eth_slug}"
    ops: list[tuple[str, Path, Path]] = []

    lib_old = REPO / "library" / old_region / country_slug / eth_slug
    lib_new = REPO / "library" / new_region / country_slug / eth_slug
    if lib_old.exists():
        ops.append(("dir", lib_old, lib_new))
    for suffix in (".md", ".long.md"):
        ops.append(("file", REPO / "content" / old_region / f"{country_slug}__{eth_slug}{suffix}",
                    REPO / "content" / new_region / f"{country_slug}__{eth_slug}{suffix}"))
    ops.append(("file", REPO / "content" / "media" / old_region / f"{country_slug}__{eth_slug}.json",
                REPO / "content" / "media" / new_region / f"{country_slug}__{eth_slug}.json"))
    for sub in ("ethnicities", "unvetted", "territories"):
        ops.append(("file", REPO / "data" / sub / f"{key}.json", REPO / "data" / sub / f"{new_key}.json"))
    ops = [o for o in ops if o[1].exists()]

    for kind, a, b in ops:
        print(f"  {kind:4} {a.relative_to(REPO)} -> {b.relative_to(REPO)}")
        if b.exists():
            sys.exit(f"target exists: {b}")

    # Seed entry: the ethnicity moves into the same country in the new
    # region's seed file (the country entry is created from the old one's
    # country-level fields when missing).
    src_seed, dst_seed = _seed_file(old_region), _seed_file(new_region)
    seed_move = None
    if src_seed:
        src = json.loads(src_seed.read_text(encoding="utf-8"))
        for c in src["countries"]:
            e = _slug_name(c, eth_slug)
            if e:
                seed_move = (src, c, e)
                break
    if seed_move:
        if not dst_seed:
            sys.exit(f"no seed file with region {new_region!r}; create data/seed/<name>.json first")
        print(f"  seed {seed_move[2]['name']}: {src_seed.name} -> {dst_seed.name}")

    if not commit:
        print("(dry run; pass --commit)")
        return

    for kind, a, b in ops:
        b.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(a), str(b))
    if lib_old.exists() is False and lib_new.exists():
        old_prefix = f"library\\{old_region}\\".replace("\\", "/")
        n = 0
        for meta in lib_new.rglob("metadata.json"):
            t = meta.read_text(encoding="utf-8")
            t2 = t.replace(f"library\\\\{old_region}\\\\", f"library\\\\{new_region}\\\\").replace(
                old_prefix, f"library/{new_region}/").replace(
                f'"region": "{old_region}"', f'"region": "{new_region}"')
            if t2 != t:
                meta.write_text(t2, encoding="utf-8")
                n += 1
        print(f"  rewrote local_path and region in {n} metadata files")
    # Writeup frontmatter names the region too ("region: \"East Asia\"", tags).
    for suffix in (".md", ".long.md"):
        md = REPO / "content" / new_region / f"{country_slug}__{eth_slug}{suffix}"
        if md.exists():
            t = md.read_text(encoding="utf-8")
            title = lambda r: r.replace("-", " ").title()
            t2 = t.replace(f'region: "{title(old_region)}"', f'region: "{title(new_region)}"').replace(
                f"tags: [ethnography, {old_region}]", f"tags: [ethnography, {new_region}]")
            if t2 != t:
                md.write_text(t2, encoding="utf-8")
    # Territory caches are keyed by culture key; renaming the key keeps the
    # Codex verdicts instead of judging the culture again.
    for p in sorted((REPO / "data" / "world").glob("territory_*")):
        t = p.read_text(encoding="utf-8")
        if f'"{key}"' in t:
            p.write_text(t.replace(f'"{key}"', f'"{new_key}"'), encoding="utf-8")
            print(f"  renamed key in {p.relative_to(REPO)}")
    if seed_move:
        src, c, e = seed_move
        c["ethnicities"].remove(e)
        if not c["ethnicities"]:
            src["countries"].remove(c)
        dst = json.loads(dst_seed.read_text(encoding="utf-8"))
        tgt = next((x for x in dst["countries"] if x["country"] == c["country"]), None)
        if tgt is None:
            tgt = {k: v for k, v in c.items() if k != "ethnicities"} | {"ethnicities": []}
            dst["countries"].append(tgt)
        tgt["ethnicities"].append(e)
        src_seed.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")
        dst_seed.write_text(json.dumps(dst, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"moved {key} -> {new_key}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key")
    ap.add_argument("region")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    move(a.key, a.region, a.commit)


if __name__ == "__main__":
    main()
