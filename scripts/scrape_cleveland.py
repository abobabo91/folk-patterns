"""Run Cleveland Museum of Art scraper across all cohort ethnicities.

Cleveland uses demonyms (Burmese vs Bamar, Cambodian vs Khmer, Chinese vs
Uyghur). We probe with both the ethnonym AND the country demonym, then let
the culture-string filter in cleveland.scrape_ethnicity discard false hits.

Extra queries + accept tokens are read from each ethnicity's seed entry:
  ethnicity.source_queries.cleveland          extra demonym / geography terms
  ethnicity.cleveland_accept_tokens           bespoke minority accept-list

Adding a new ethnicity = one seed diff, no code touch."""
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
from folk_patterns.museums import cleveland

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region", nargs="?", help="Region slug (default: all)")
    ap.add_argument("--only", help="Only run for this ethnicity (substring)")
    ap.add_argument("--min-existing", type=int, default=1000,
                    help="Skip ethnicities that already have this many objects (default: never skip — always augment)")
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
                extras = (eth.get("source_queries") or {}).get("cleveland") or []
                accept_tokens = eth.get("cleveland_accept_tokens") or []
                queries = [ethnicity] + extras + eth["traditions"][:3]
                print(f"[cle ] {region} / {country} / {ethnicity}  queries={len(queries)}", flush=True)
                try:
                    n = cleveland.scrape_ethnicity(
                        client, region, country, ethnicity, queries,
                        seed_accept_tokens=accept_tokens,
                    )
                except Exception as e:
                    print(f"  ! failed: {e}", flush=True)
                    continue
                print(f"  -> saved {n} records", flush=True)

    client.close()


if __name__ == "__main__":
    main()
