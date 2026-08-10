"""End-to-end pipeline to add a new ethnicity to the atlas.

    python scripts/add_culture.py --name "Wayuu" --country "Colombia" --region latin_america

Steps:
  1. If the target region seed file doesn't exist, bail (creating a whole
     new region is a separate operation — see scripts/_generate_region.py).
  2. LLM drafts the seed entry (homeland, traditions, commons categories,
     source_queries).
  3. Human review of the draft (unless --yes).
  4. Insert into data/seed/<region>.json under the right country.
  5. Ambiguity probe — bare-word Europeana search + LLM review. If any risky
     matches found, print the suggested reject regex and pause for human
     review before scraping.
  6. Run scrape_all.py --only <name> to actually scrape.
  7. Sample review — LLM audits 12 random scraped records. If contamination
     found, print the suggested fix.
  8. Generate writeup via existing generate_writeup.py.
  9. Rebuild index via existing build_index.py.
 10. Print completeness stats for the new ethnicity.

Every step is idempotent-ish: re-running skips work already done.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

from slugify import slugify

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _draft_seed_entry as draft_mod
import _probe_ambiguity as probe_mod
import _review_sample as review_mod
import _generate_region as gen_region
import _patch_europeana_reject as patcher


ROOT = Path(__file__).resolve().parent.parent
SEED_DIR = ROOT / "data" / "seed"


def _confirm(msg: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    print(f"\n{msg} [y/N]: ", end="", flush=True)
    return input().strip().lower() == "y"


def _load_seed(region: str, region_display: str | None, countries_for_new: list[str] | None, auto_yes: bool) -> tuple[Path, dict]:
    p = SEED_DIR / f"{region}.json"
    if p.exists():
        return p, json.loads(p.read_text(encoding="utf-8"))
    # Region seed doesn't exist — auto-create via _generate_region if we have
    # enough context (--region-display + --region-countries).
    if not region_display or not countries_for_new:
        raise SystemExit(
            f"! seed file {p} does not exist. Pass --region-display "
            f"and --region-countries to auto-create it, or run "
            f"scripts/_generate_region.py manually first."
        )
    print(f"[0/6] Region seed {p.name} not found — drafting via claude …")
    seed_obj = gen_region.draft(region, region_display, countries_for_new)
    print(json.dumps(seed_obj, indent=2, ensure_ascii=False)[:2000] + "\n… (truncated)")
    if not _confirm(f"Accept and save to {p}?", auto_yes):
        raise SystemExit("aborted")
    p.write_text(json.dumps(seed_obj, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote {p}")
    return p, seed_obj


def _country_entry(seed: dict, country: str) -> dict:
    for c in seed["countries"]:
        if c["country"].lower() == country.lower():
            return c
    # Country not present in seed — create it, but this is a bigger move so
    # warn.
    print(f"! country {country!r} not in {seed['region']} seed. Adding new "
          f"country entry — you may want to set met_queries / met_gate_tokens.")
    new = {
        "country": country,
        "met_queries": [country],
        "majority_ethnicity": None,   # set after first ethnicity is added
        "met_gate_tokens": [],
        "ethnicities": [],
    }
    seed["countries"].append(new)
    return new


def _already_in_seed(country_entry: dict, name: str) -> bool:
    return any(e["name"].lower() == name.lower() for e in country_entry["ethnicities"])


def _add_to_seed(seed_path: Path, seed: dict, country_entry: dict, entry: dict) -> None:
    if country_entry["majority_ethnicity"] is None:
        country_entry["majority_ethnicity"] = entry["name"]
    country_entry["ethnicities"].append(entry)
    seed_path.write_text(json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8")


def _run(cmd: list[str], desc: str) -> int:
    print(f"\n{'='*60}\n {desc}\n {' '.join(cmd)}\n{'='*60}\n", flush=True)
    return subprocess.call(cmd)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="Ethnicity name (canonical, e.g. 'Wayuu')")
    ap.add_argument("--country", required=True)
    ap.add_argument("--region", required=True, help="region slug matching data/seed/<region>.json")
    ap.add_argument("--yes", "-y", action="store_true", help="Skip all confirmations (batch mode)")
    ap.add_argument("--skip-scrape", action="store_true", help="Draft seed + probe only, no scrape")
    ap.add_argument("--skip-review", action="store_true", help="Skip post-scrape LLM sample review")
    ap.add_argument("--skip-writeup", action="store_true")
    ap.add_argument("--skip-index", action="store_true")
    ap.add_argument("--region-display", help="Display name for a NEW region, e.g. 'Latin America'")
    ap.add_argument("--region-countries", help="Comma-separated country list for a NEW region")
    args = ap.parse_args()

    seed_path, seed = _load_seed(
        args.region, args.region_display,
        [c.strip() for c in (args.region_countries or "").split(",") if c.strip()],
        args.yes,
    )
    country_entry = _country_entry(seed, args.country)

    # 1. Draft seed entry (or skip if already present)
    if _already_in_seed(country_entry, args.name):
        print(f"[1/6] {args.name!r} already in seed — skipping draft")
    else:
        print(f"[1/6] Drafting seed entry for {args.name} ({args.country}, {args.region}) via claude …")
        entry = draft_mod.draft(args.name, args.country, args.region)
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        if not _confirm("Accept this seed entry and add to seed file?", args.yes):
            print("aborted"); return
        _add_to_seed(seed_path, seed, country_entry, entry)
        print(f"  wrote {seed_path}")

    # 2. Ambiguity probe (with auto-commit of the suggested reject regex)
    print(f"\n[2/6] Ambiguity probe (bare-word Europeana search + LLM review) …")
    probe = probe_mod.probe(args.name, args.country, args.region, top_n=20)
    off = probe.get("off_topic_indices") or []
    regex = probe.get("reject_regex") or ""
    if off and regex:
        print(f"  ! LLM flagged {len(off)} off-topic hits.")
        print(f"  reasoning: {probe.get('reasoning')}")
        patched = patcher.patch(args.name, regex, yes=args.yes)
        if patched:
            print(f"  ✓ europeana.py patched with new reject pattern")
    elif off:
        print(f"  ! LLM flagged {len(off)} off-topic hits but no regex to auto-apply.")
    else:
        print("  no ambiguity risk detected.")

    if args.skip_scrape:
        print("\n[skip] scrape phase skipped by flag")
        return

    # 3. Scrape
    rc = _run(
        [sys.executable, str(SEED_DIR.parent.parent / "scripts" / "scrape_all.py"),
         args.region, "--only", args.name],
        f"[3/6] Scraping {args.name}",
    )
    if rc != 0:
        print(f"! scrape returned {rc}; continuing to review + writeup")

    # 4. Sample review
    if args.skip_review:
        print("[skip] sample review skipped by flag")
    else:
        slug = slugify(args.name)
        print(f"\n[4/6] Post-scrape LLM sample review for {slug!r} …")
        try:
            review = review_mod.review(args.name, args.country, slug, n=12)
        except Exception as e:
            print(f"  ! review failed: {e}"); review = {}
        off_count = review.get("off_topic_count", 0)
        print(f"  off-topic records: {off_count}")
        if off_count:
            regex = review.get("suggested_reject_regex") or ""
            if regex:
                patched = patcher.patch(args.name, regex, yes=args.yes)
                if patched:
                    print(f"  ✓ europeana.py patched with post-scrape reject pattern")
            print(f"  suggested seed removals: {review.get('suggested_seed_removals')}")
            print(f"  summary: {review.get('summary')}")

    # 5. Writeup
    if args.skip_writeup:
        print("[skip] writeup skipped by flag")
    else:
        _run(
            [sys.executable, str(ROOT / "scripts" / "generate_writeup.py"), args.name],
            f"[5/6] Generating writeup for {args.name}",
        )

    # 6. Index rebuild
    if args.skip_index:
        print("[skip] index skipped by flag")
    else:
        _run(
            [sys.executable, str(ROOT / "scripts" / "build_index.py")],
            f"[6/6] Rebuilding site index",
        )

    # 7. Final completeness stat for this ethnicity
    slug = slugify(args.name)
    total_imgs = 0
    total_recs = 0
    for meta in (ROOT / "library").rglob("metadata.json"):
        parts = meta.relative_to(ROOT / "library").parts
        if len(parts) < 5 or parts[2] != slug:
            continue
        try:
            recs = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            recs = []
        total_recs += len(recs)
        imgs_dir = meta.parent / "images"
        if imgs_dir.exists():
            total_imgs += sum(1 for f in imgs_dir.iterdir() if f.is_file())
    print(f"\n== {args.name} added ==")
    print(f"  Records: {total_recs}")
    print(f"  Images:  {total_imgs}")


if __name__ == "__main__":
    main()
