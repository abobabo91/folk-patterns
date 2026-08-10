"""For each (country, ethnicity) in every region seed, ask Claude to draft an
encyclopedic markdown writeup. Save to content/<region>/<country>__<ethnicity>.md.

Idempotent — skips writeups that already exist unless --force is passed.

Usage:
    python scripts/generate_writeups.py                    # all regions
    python scripts/generate_writeups.py central_asia       # one region
    python scripts/generate_writeups.py central_asia --force
    python scripts/generate_writeups.py central_asia --only "Uzbek"
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import DATA_DIR
from folk_patterns.writeup import generate_writeup
from slugify import slugify


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = REPO_ROOT / "content"
MEDIA_DIR = REPO_ROOT / "content" / "media"


def _load_grounding(region: str, country: str, ethnicity: str) -> tuple[dict | None, list[dict] | None]:
    """Read the media sidecar (if it exists) and return (wiki_dict, ich_list).
    Returns (None, None) when no sidecar — writeup then runs ungrounded."""
    sidecar = MEDIA_DIR / slugify(region) / f"{slugify(country)}__{slugify(ethnicity)}.json"
    if not sidecar.exists():
        return None, None
    b = json.loads(sidecar.read_text(encoding="utf-8"))
    srcs = b.get("sources") or {}
    return srcs.get("wikipedia"), srcs.get("unesco_ich")


def load_seed(region_slug: str) -> dict:
    p = DATA_DIR / "seed" / f"{region_slug}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def writeup_path(region: str, country: str, ethnicity: str) -> Path:
    return CONTENT_DIR / slugify(region) / f"{slugify(country)}__{slugify(ethnicity)}.md"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region", nargs="?", help="Region slug (defaults: all under data/seed/)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing writeups")
    ap.add_argument("--only", help="Only generate for this ethnicity (case-insensitive substring)")
    args = ap.parse_args()

    if args.region:
        regions = [args.region]
    else:
        regions = [p.stem for p in (DATA_DIR / "seed").glob("*.json")]

    CONTENT_DIR.mkdir(exist_ok=True)

    for region_slug in regions:
        seed = load_seed(region_slug)
        region = seed["region"]
        needle = (args.only or "").lower()

        for country_entry in seed["countries"]:
            country = country_entry["country"]
            for eth in country_entry["ethnicities"]:
                ethnicity = eth["name"]
                if needle and needle not in ethnicity.lower():
                    continue
                out_path = writeup_path(region, country, ethnicity)
                if out_path.exists() and not args.force:
                    print(f"[skip] {region} / {country} / {ethnicity} — already exists")
                    continue
                wiki, ich = _load_grounding(region, country, ethnicity)
                mode = "grounded" if (wiki or ich) else "ungrounded"
                print(f"[gen ] {region} / {country} / {ethnicity} ({mode}) ...", flush=True)
                try:
                    md = generate_writeup(country, ethnicity, region, eth["traditions"], wiki=wiki, ich=ich)
                except Exception as e:
                    print(f"  ! failed: {e}", flush=True)
                    continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(md, encoding="utf-8")
                print(f"  -> wrote {out_path.relative_to(REPO_ROOT)}  ({len(md)} chars)", flush=True)


if __name__ == "__main__":
    main()
