"""Run British Museum scraper across all cohort ethnicities.

BM's Cloudflare-gated `_search` endpoint requires curl_cffi (browser TLS
impersonation). Uses seed source_queries.british_museum (or falls back to
seed traditions + ethnicity name).

Usage:
    python scripts/scrape_british_museum.py <region> [--only <ethnicity>]
    python scripts/scrape_british_museum.py sub_saharan_africa --only Yoruba
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from folk_patterns.util import DATA_DIR
from folk_patterns.museums import british_museum


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region", nargs="?")
    ap.add_argument("--only", help="Only run for this ethnicity")
    args = ap.parse_args()

    from _only_match import matches as _only_matches

    seed_dir = DATA_DIR / "seed"
    regions = [args.region] if args.region else [p.stem for p in seed_dir.glob("*.json")]

    client = british_museum._client()

    for region_slug in regions:
        seed = json.loads((seed_dir / f"{region_slug}.json").read_text(encoding="utf-8"))
        region = seed["region"]
        for country_entry in seed["countries"]:
            country = country_entry["country"]
            for eth in country_entry["ethnicities"]:
                ethnicity = eth["name"]
                if args.only and not _only_matches(args.only, ethnicity, country, region):
                    continue
                # Build queries: prefer seed source_queries.british_museum,
                # else use ethnicity name + top 3 traditions
                seed_bm = (eth.get("source_queries") or {}).get("british_museum") or []
                if seed_bm:
                    queries = list(seed_bm)
                else:
                    queries = [ethnicity] + eth["traditions"][:3]
                # BM search is broad — prepend the ethnonym to disambiguate
                if ethnicity.lower() not in queries[0].lower():
                    queries = [ethnicity] + queries[:5]
                # Attribution filter — reuse cleveland_accept_tokens from
                # seed (same shape: narrow ethnonym + close-subgroup tokens).
                # BM's search relevance is loose enough that without a strict
                # post-filter, unrelated records leak through.
                accept_tokens = eth.get("cleveland_accept_tokens") or []
                # Also pass tradition terms — many BM records identify
                # material by object-type keyword (aso oke, adire, tongkonan)
                # not by ethnonym, so an ethnonym-only filter drops them.
                tradition_tokens = eth.get("traditions") or []
                print(f"[bm  ] {region} / {country} / {ethnicity}  queries={len(queries)}  accept_tokens={len(accept_tokens)}+{len(tradition_tokens)} trad", flush=True)
                try:
                    n = british_museum.scrape_ethnicity(
                        client, region, country, ethnicity, queries,
                        max_per_query=100, max_total=60,
                        accept_tokens=accept_tokens,
                        tradition_tokens=tradition_tokens,
                    )
                except Exception as e:
                    print(f"  ! bm scrape failed: {e}", flush=True)
                    continue
                print(f"  -> saved {n} records", flush=True)
                time.sleep(0.4)  # polite pacing


if __name__ == "__main__":
    main()
