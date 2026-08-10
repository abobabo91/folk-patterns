"""One-off: pull the hardcoded region dicts in src/folk_patterns/places.py
into each seed JSON's `region_places` block. Idempotent — re-running is a no-op
if the seed already has region_places.

After this runs, adding a new region is a pure data operation."""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.places import _LEGACY_REGIONS

SEED = Path(__file__).resolve().parents[1] / "data" / "seed"

MAPPING = {
    "central_asia":         "central_asia",
    "middle_east_north_africa": "mena",
    "southeast_asia":       "southeast_asia",
    "sub_saharan_africa":   "sub_saharan_africa",
    "central-asia":         "central_asia",
    "southeast-asia":       "southeast_asia",
    "sub-saharan-africa":   "sub_saharan_africa",
    "middle-east-north-africa": "mena",
}


def region_block(legacy: dict) -> dict:
    return {
        "place_to_country": dict(legacy.get("place_to_country") or {}),
        "reject_places": sorted(list(legacy.get("reject_places") or [])),
        "signature_traditions": dict(legacy.get("signature_traditions") or {}),
    }


for region_key, seed_stem in MAPPING.items():
    legacy = _LEGACY_REGIONS.get(region_key)
    if not legacy:
        continue
    seed_path = SEED / f"{seed_stem}.json"
    if not seed_path.exists():
        continue
    d = json.loads(seed_path.read_text(encoding="utf-8"))
    if "region_places" in d:
        continue   # already migrated
    d["region_places"] = region_block(legacy)
    seed_path.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"migrated {seed_path.name}: {len(d['region_places']['place_to_country'])} places")
