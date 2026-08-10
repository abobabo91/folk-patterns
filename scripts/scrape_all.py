"""Run every wired museum scraper for a region.

    python scripts/scrape_all.py central_asia
    python scripts/scrape_all.py central_asia --only Uzbek

This is the ONE command a new contributor needs to know. It calls, in order:
    1. scrape_region.py      (Met + V&A + Rijks + Smithsonian)
    2. scrape_cleveland.py   (Cleveland Museum of Art)
    3. scrape_british_museum.py (British Museum)
    4. scrape_europeana.py   (Europeana aggregator, min-existing bug-safe)
    5. scrape_commons_arch.py (Wikimedia Commons architecture)

Order matters: scrape_region runs first because it's the broadest sweep and
seeds the library structure; the others are gap-fillers. Europeana runs late
because its --min gate checks for Europeana-specific existing records.

Each downstream scraper is idempotent (raw caches + image-existence checks),
so re-running scrape_all is safe.
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable


def run(script: str, extra: list[str]) -> int:
    cmd = [PYTHON, str(SCRIPTS_DIR / script), *extra]
    print(f"\n{'='*60}\n {' '.join(cmd)}\n{'='*60}\n", flush=True)
    return subprocess.call(cmd)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region", help="Region slug (matches data/seed/<region>.json)")
    ap.add_argument("--only", help="Only run for this ethnicity name")
    ap.add_argument("--skip", default="",
                    help="Comma-separated scrapers to skip: region,cleveland,bm,europeana,commons")
    args = ap.parse_args()

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    only_arg = ["--only", args.only] if args.only else []

    steps = [
        ("region",    "scrape_region.py",         [args.region, "--museums=met,va,rijks,si"]),
        ("cleveland", "scrape_cleveland.py",      [args.region, *only_arg]),
        ("bm",        "scrape_british_museum.py", [args.region, *only_arg]),
        # --min 99999 because per-ethnicity Europeana counts are the right
        # skip-gate. Without this the driver would skip any ethnicity that
        # already had *any* Met/V&A/BM records — the 2026-07 silent-drop bug.
        ("europeana", "scrape_europeana.py",      [args.region, "--min", "99999", *only_arg]),
        ("commons",   "scrape_commons_arch.py",   [args.region, *only_arg]),
    ]

    for key, script, extra in steps:
        if key in skip:
            print(f"\n[skip] {script}", flush=True)
            continue
        rc = run(script, extra)
        if rc != 0:
            print(f"  ! {script} exited {rc}", flush=True)


if __name__ == "__main__":
    main()
