"""Fill gaps in the museum-object library using Europeana.

Reads all seed ethnicities, checks which ones have 0 (or fewer than N)
existing objects in the library, and runs Europeana search for each. Search
queries are built from the ethnicity name + its top seed traditions.

Idempotent-ish: skips ethnicities that already meet the min-object threshold
unless --force. Raw Europeana responses are cached under data/raw/europeana/
so re-runs are fast (or you can `rm` the cache to re-hit the API).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import DATA_DIR, RateLimitedClient, LIBRARY_DIR
from folk_patterns.museums import europeana

REPO_ROOT = Path(__file__).resolve().parents[1]


def _existing_counts() -> dict[tuple[str, str, str], int]:
    """Count existing EUROPEANA records per (region, country, ethnicity).

    Historical bug: this used to count records from *any* museum, so an
    ethnicity with 100 Met records but 0 Europeana records got skipped
    when Europeana had never been run for it. Now scoped to europeana."""
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for meta in LIBRARY_DIR.glob("*/*/*/*/*/metadata.json"):
        recs = json.loads(meta.read_text(encoding="utf-8"))
        for r in recs:
            if (r.get("source") or {}).get("museum") != "europeana":
                continue
            cul = r.get("cultural") or {}
            key = (cul.get("region") or "", cul.get("country") or "", cul.get("ethnicity") or "")
            counts[key] += 1
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region", nargs="?", help="Region slug (default: all)")
    ap.add_argument("--min", type=int, default=1, help="Skip ethnicities with at least this many existing objects")
    ap.add_argument("--only", help="Only run for this ethnicity (substring)")
    args = ap.parse_args()

    seed_dir = DATA_DIR / "seed"
    regions = [args.region] if args.region else [p.stem for p in seed_dir.glob("*.json")]
    counts = _existing_counts()

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
                key = (region, country, ethnicity)
                existing = counts.get(key, 0)
                if existing >= args.min:
                    print(f"[skip] {region} / {country} / {ethnicity} — already has {existing} objects")
                    continue
                # Build queries: seed-provided source_queries.europeana
                # take precedence over the generic ethnicity+traditions
                # default. Adding new colonial-language variants for a group
                # is a single seed diff (see scripts/expand_queries.py).
                seed_eu = (eth.get("source_queries") or {}).get("europeana") or []
                if seed_eu:
                    queries = list(seed_eu) + eth["traditions"][:2]
                else:
                    queries = [ethnicity] + eth["traditions"][:4]
                    # For known ambiguous ethnonyms, prepend a country hint.
                    if ethnicity.lower() in {"cham", "malay", "hmong", "kinh", "batak", "chin"}:
                        queries = [f'"{ethnicity}" {country}'] + eth["traditions"][:4]
                print(f"[eur ] {region} / {country} / {ethnicity} — queries: {queries}", flush=True)
                try:
                    n = europeana.scrape_ethnicity(
                        client, region, country, ethnicity, queries,
                        seed_traditions=eth["traditions"],
                    )
                except Exception as e:
                    print(f"  ! failed: {e}", flush=True)
                    continue
                print(f"  -> saved {n} records", flush=True)

    client.close()


if __name__ == "__main__":
    main()
