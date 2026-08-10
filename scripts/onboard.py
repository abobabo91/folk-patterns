"""Single-command ethnicity onboarding.

Runs the full pipeline for one or more ethnicities:
  1. Expand queries via Claude CLI (writes to seed JSON, auto-clears caches)
  2. Run applicable scrapers (europeana, commons_arch, cleveland by default)
  3. Upload new images to R2
  4. Rebuild index + sync site data
  5. Run expand_classifier on new unclassified records
  6. Print a per-ethnicity report

Usage:
  python scripts/onboard.py Uyghur Toraja        # onboard two ethnicities
  python scripts/onboard.py --scrapers europeana,commons_arch Uyghur
  python scripts/onboard.py --skip-expand Uyghur # queries already good, just re-scrape

Assumes the seed JSON entry exists (ethnicity is listed under some
data/seed/<region>.json). For a completely NEW region, create the seed
skeleton first, THEN run onboard on each ethnicity.
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def run(cmd: list[str], label: str) -> bool:
    """Run a subprocess, streaming output. Return True on success."""
    print(f"\n▸ {label}")
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT))
    ok = proc.returncode == 0
    if not ok:
        print(f"  ! failed (exit {proc.returncode})")
    return ok


def per_ethnicity_report(ethnicity: str) -> None:
    """Print current shard stats for one ethnicity."""
    import glob, json
    for p in glob.glob(str(REPO_ROOT / "data" / "ethnicities" / "*.json")):
        sh = json.loads(Path(p).read_text(encoding="utf-8"))
        if sh["ethnicity"] != ethnicity:
            continue
        afs = sh.get("art_form_buckets") or {}
        total = sum(len(v) for v in afs.values())
        by = ", ".join(f"{af}:{len(v)}" for af, v in sorted(afs.items(), key=lambda x: -len(x[1])) if v)
        print(f"  {sh['country']}/{sh['ethnicity']}  →  {total} tiles  ({by})")
        return
    print(f"  ({ethnicity}: no shard found)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ethnicities", nargs="+", help="Ethnicity names (as they appear in seed JSON)")
    ap.add_argument("--scrapers", default="europeana,commons_arch,cleveland",
                    help="Comma-separated list. Options: europeana, commons_arch, cleveland (default: all three)")
    ap.add_argument("--skip-expand", action="store_true",
                    help="Skip expand_queries — use existing seed queries as-is")
    ap.add_argument("--skip-upload", action="store_true",
                    help="Skip R2 upload (dev iteration)")
    ap.add_argument("--skip-classify", action="store_true",
                    help="Skip expand_classifier pass")
    ap.add_argument("--skip-writeup", action="store_true",
                    help="Skip auto-generation of markdown writeups")
    args = ap.parse_args()

    scrapers = [s.strip() for s in args.scrapers.split(",") if s.strip()]

    # Step 0: generate writeups (idempotent — skips already-written).
    # Runs BEFORE scraping because writeups don't depend on scraped data;
    # this way the site has a writeup ready even if a scrape fails.
    if not args.skip_writeup:
        run(
            ["python", str(SCRIPTS / "generate_writeup.py")] + args.ethnicities,
            "generate writeups (skips those already written)",
        )

    # Step 1: expand_queries (also auto-clears caches for affected ethnicities)
    if not args.skip_expand:
        run(
            ["python", str(SCRIPTS / "expand_queries.py")] + args.ethnicities + ["--apply"],
            "expand queries + auto-clear caches",
        )

    # Step 2: run each scraper --only <ethnicity>
    for eth in args.ethnicities:
        for scraper in scrapers:
            script = {
                "europeana": "scrape_europeana.py",
                "commons_arch": "scrape_commons_arch.py",
                "cleveland": "scrape_cleveland.py",
            }.get(scraper)
            if not script:
                print(f"! unknown scraper: {scraper}")
                continue
            extra = ["--min", "999999"] if scraper == "europeana" else []
            run(
                ["python", str(SCRIPTS / script), "--only", eth] + extra,
                f"scrape {scraper} for {eth}",
            )

    # Step 3: upload to R2
    if not args.skip_upload:
        run(
            ["python", str(SCRIPTS / "upload_to_r2.py"), "--commit"],
            "upload new images to R2",
        )

    # Step 4: rebuild index
    run(["python", str(SCRIPTS / "build_index.py")], "rebuild data/index.json and shards")

    # Step 5: sync site data
    run(["node", str(REPO_ROOT / "site" / "scripts" / "sync-public.mjs")], "sync data to site/public")

    # Step 6: classifier expansion
    if not args.skip_classify:
        run(
            ["python", str(SCRIPTS / "expand_classifier.py"), "--apply"],
            "expand_classifier (reclassify new unclassifieds + reject junk)",
        )
        # Rebuild once more to apply the new overrides
        run(["python", str(SCRIPTS / "build_index.py")], "rebuild (post-classifier)")
        run(["node", str(REPO_ROOT / "site" / "scripts" / "sync-public.mjs")], "sync data to site/public")

    # Report
    print("\n\n=== onboarding complete ===")
    for eth in args.ethnicities:
        per_ethnicity_report(eth)


if __name__ == "__main__":
    main()
