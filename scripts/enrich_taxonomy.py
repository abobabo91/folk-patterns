"""Expand seed regional taxonomy by asking Claude (CLI) for additional named
traditions per (country, ethnicity). Writes result to data/taxonomy/<region>.json.

Idempotent — reruns re-enrich only entries not yet in the output file. Use
--force to redo all.
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
from folk_patterns.enrich import enrich_ethnicity


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    seed_path = DATA_DIR / "seed" / f"{args.region}.json"
    out_path = DATA_DIR / "taxonomy" / f"{args.region}.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))

    if out_path.exists() and not args.force:
        current = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        current = {"region": seed["region"], "countries": []}

    done_keys = {
        (c["country"], e["name"])
        for c in current["countries"]
        for e in c["ethnicities"]
    }

    for country_entry in seed["countries"]:
        country = country_entry["country"]
        out_country = next(
            (c for c in current["countries"] if c["country"] == country), None
        )
        if out_country is None:
            out_country = {"country": country, "met_queries": country_entry.get("met_queries", []), "ethnicities": []}
            current["countries"].append(out_country)

        for eth in country_entry["ethnicities"]:
            if (country, eth["name"]) in done_keys and not args.force:
                print(f"[skip] {country} / {eth['name']} — already enriched")
                continue
            print(f"[enrich] {country} / {eth['name']} ({len(eth['traditions'])} seed) ...")
            try:
                traditions = enrich_ethnicity(country, eth["name"], seed["region"], eth["traditions"])
            except Exception as e:
                print(f"  ! failed: {e}")
                continue
            out_country["ethnicities"] = [
                x for x in out_country["ethnicities"] if x["name"] != eth["name"]
            ]
            out_country["ethnicities"].append(
                {"name": eth["name"], "traditions": traditions}
            )
            print(f"  -> {len(traditions)} traditions")
            # Write after each ethnicity so we don't lose progress.
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
