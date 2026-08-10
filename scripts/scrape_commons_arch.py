"""Run the Commons architectural scraper for every ethnicity in seed."""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import DATA_DIR, RateLimitedClient
from folk_patterns.museums import commons_arch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region", nargs="?")
    ap.add_argument("--only", help="Only run for this ethnicity (substring)")
    args = ap.parse_args()

    seed_dir = DATA_DIR / "seed"
    regions = [args.region] if args.region else [p.stem for p in seed_dir.glob("*.json")]
    client = RateLimitedClient(min_interval_s=0.35)
    from _only_match import matches as _only_matches
    needle = args.only or ""

    for region_slug in regions:
        seed = json.loads((seed_dir / f"{region_slug}.json").read_text(encoding="utf-8"))
        region = seed["region"]
        for country_entry in seed["countries"]:
            country = country_entry["country"]
            for eth in country_entry["ethnicities"]:
                ethnicity = eth["name"]
                if needle and not _only_matches(needle, ethnicity, country, region):
                    continue
                cats = commons_arch.ARCH_CATEGORIES.get(ethnicity, [])
                if not cats:
                    continue
                # Scale max_total by category count so every category gets
                # sampled at least a few times. Prevents the situation where
                # a culture has 30 Commons categories but only the first
                # ~5 get scraped before the total cap fires.
                cap = max(60, min(300, len(cats) * 8))
                print(f"[carch] {region} / {country} / {ethnicity}  cats={len(cats)}  cap={cap}", flush=True)
                try:
                    n = commons_arch.scrape_ethnicity(client, region, country, ethnicity, max_total=cap)
                except Exception as e:
                    print(f"  ! failed: {e}", flush=True)
                    continue
                print(f"  -> saved {n} records", flush=True)

    client.close()


if __name__ == "__main__":
    main()
