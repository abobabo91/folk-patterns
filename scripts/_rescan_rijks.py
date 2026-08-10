"""One-off: re-run the Rijksmuseum scraper across every seed tradition,
using the existing raw cache. Prints per-tradition counts."""
import glob
import io
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import RateLimitedClient
from folk_patterns.museums import rijks


def main() -> None:
    with RateLimitedClient(min_interval_s=0.2) as client:
        for sp in sorted(glob.glob("data/seed/*.json")):
            seed = json.load(open(sp, encoding="utf-8"))
            region = seed["region"]
            trad_map = {}
            for c in seed["countries"]:
                for e in c["ethnicities"]:
                    for t in e.get("traditions") or []:
                        trad_map.setdefault(t, {})[c["country"]] = e["name"]
            for trad, ce_map in trad_map.items():
                # Check that a cache exists (avoids network re-fetch)
                from slugify import slugify
                cache_slug = slugify(f"rijks__{trad}")
                cache_path = Path("data/raw/rijks") / f"{cache_slug}.json"
                if not cache_path.exists():
                    continue
                try:
                    result = rijks.scrape_tradition_routed(
                        client=client, region=region, tradition_name=trad,
                        tradition_ethnicity_by_country=ce_map, max_per_country=8,
                    )
                except Exception as e:
                    print(f"  ! {trad}: {e}", flush=True)
                    continue
                if result:
                    print(f"  [{region[:14]}] {trad!r}: {result}", flush=True)

    total = 0
    for f in glob.glob("library/**/metadata.json", recursive=True):
        try:
            for r in json.load(open(f, encoding="utf-8")):
                if r["source"]["museum"] == "rijks":
                    total += 1
        except Exception:
            pass
    print(f"\nrijks records total: {total}")


if __name__ == "__main__":
    main()
