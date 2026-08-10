"""For each ethnicity in the seed, fetch Wikipedia + Commons + UNESCO ICH +
Folkways references. Write one JSON sidecar per ethnicity at
content/media/<region>/<country>__<ethnicity>.json.

Consumed by:
  - scripts/generate_writeups.py (--grounded): reads sidecar.sources.wikipedia
    + sources.unesco_ich as grounding context for the LLM prompt.
  - scripts/build_index.py: merges sidecar into the per-ethnicity shard so the
    frontend can render photo gallery / UNESCO cards / Folkways audio links.

Idempotent — skips ethnicities whose sidecar already exists unless --force.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tomllib
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import DATA_DIR
from folk_patterns.media import fetch_bundle
from slugify import slugify

REPO_ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = REPO_ROOT / "content" / "media"
VAULT_PATH = Path("C:/Users/abele/Desktop/github/tools/vault/vault.toml")


def _load_smithsonian_key() -> str | None:
    """Read the api.data.gov / Smithsonian key from the personal vault so
    Folkways search is available. Return None if the vault isn't reachable."""
    if not VAULT_PATH.exists():
        return None
    try:
        v = tomllib.load(open(VAULT_PATH, "rb"))
        return v.get("apis", {}).get("smithsonian", {}).get("key")
    except Exception:
        return None


def sidecar_path(region: str, country: str, ethnicity: str) -> Path:
    return MEDIA_DIR / slugify(region) / f"{slugify(country)}__{slugify(ethnicity)}.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region", nargs="?", help="Region slug (default: all)")
    ap.add_argument("--only", help="Only fetch for this ethnicity (case-insensitive substring)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing sidecars")
    args = ap.parse_args()

    seed_dir = DATA_DIR / "seed"
    regions = [args.region] if args.region else [p.stem for p in seed_dir.glob("*.json")]
    sm_key = _load_smithsonian_key()
    if not sm_key:
        print("[warn] Smithsonian/Folkways API key not found in vault; Folkways step will be skipped.")

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    needle = (args.only or "").lower()

    for region_slug in regions:
        seed = json.loads((seed_dir / f"{region_slug}.json").read_text(encoding="utf-8"))
        region = seed["region"]
        for country_entry in seed["countries"]:
            country = country_entry["country"]
            for eth in country_entry["ethnicities"]:
                ethnicity = eth["name"]
                if needle and needle not in ethnicity.lower():
                    continue
                out = sidecar_path(region, country, ethnicity)
                if out.exists() and not args.force:
                    print(f"[skip] {region} / {country} / {ethnicity} — sidecar exists")
                    continue
                print(f"[fetch] {region} / {country} / {ethnicity} ...", flush=True)
                bundle = fetch_bundle(country, ethnicity, eth["traditions"], folkways_api_key=sm_key)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
                wiki = bundle["sources"].get("wikipedia") or {}
                commons = bundle["sources"].get("commons") or []
                ich = bundle["sources"].get("unesco_ich") or []
                fw = bundle["sources"].get("folkways") or []
                print(f"  -> wiki:{wiki.get('title') or '-'} | commons:{len(commons)} | ich:{len(ich)} | folkways:{len(fw)}", flush=True)


if __name__ == "__main__":
    main()
