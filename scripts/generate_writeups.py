"""For each (country, ethnicity) in every region seed, draft an encyclopedic
markdown writeup from its sources. Save to content/<region>/<country>__<ethnicity>.md.

Sources: the media sidecar's Wikipedia article and UNESCO ICH list, related
Wikipedia articles ("Culture of the X", "X art", ...; missing ones skipped),
and the museum catalogue text of the culture's library records. The writer may
use nothing else. Each draft is audited: italic terms and numbers that appear
in no source are sent back once as objections; what survives the retry is
written anyway, printed, and logged to data/writeup_audit.jsonl for review.
Measured before this: the Asmat and Tlingit drafts (2026-09-27/28) each used
about ten vernacular terms found in no source.

Idempotent — skips writeups that already exist unless --force is passed.

Usage:
    python scripts/generate_writeups.py                    # all regions
    python scripts/generate_writeups.py central_asia       # one region
    python scripts/generate_writeups.py central_asia --force
    python scripts/generate_writeups.py central_asia --only "Uzbek"
    python scripts/generate_writeups.py east_asia --only Ainu --force --export-batch w001
    python scripts/generate_writeups.py --import-batch w001
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import DATA_DIR
from folk_patterns.media import wiki_fetch_article
from folk_patterns.writeup import (EXTRA_ARTICLES, build_writeup_prompt, generate_writeup, grounding_sources_text,
                                   museum_records_text, unsupported)
from slugify import slugify


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = REPO_ROOT / "content"
MEDIA_DIR = REPO_ROOT / "content" / "media"
AUDIT_LOG = DATA_DIR / "writeup_audit.jsonl"
BATCH_DIR = DATA_DIR / "writeup_batches"
RESULT_DIR = DATA_DIR / "writeup_results"
RESTRUCTURE_LOG = DATA_DIR / "writeup_restructure.jsonl"


def batch_path(directory: Path, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError("Batch name must contain only letters, digits, hyphens or underscores")
    return directory / f"{name}.jsonl"


def import_batch(name: str, force: bool = False, results_path: Path | None = None) -> None:
    """Apply finished cloud drafts and their audit records without calling a model."""
    rows = {r["id"]: r for r in (json.loads(line) for line in
            batch_path(BATCH_DIR, name).read_text(encoding="utf-8").splitlines() if line.strip())}
    result_file = results_path or batch_path(RESULT_DIR, name)
    imported = skipped = 0
    for line in result_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        result = json.loads(line)
        row = rows[result["id"]]
        out_path = writeup_path(row["region"], row["country"], row["ethnicity"])
        if out_path.exists() and not force:
            print(f"[skip] {row['region']} / {row['country']} / {row['ethnicity']} — already exists")
            skipped += 1
            continue
        long_md = result["long_markdown"]
        short_md = result["short_markdown"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        long_path = out_path.with_suffix(".long.md")
        if short_md is not None:
            long_path.write_text(long_md, encoding="utf-8")
            out_path.write_text(short_md + "\n", encoding="utf-8")
        else:
            out_path.write_text(long_md, encoding="utf-8")
            long_path.unlink(missing_ok=True)
        if row["mode"] == "grounded":
            seed = load_seed(row["region_slug"])
            eth = next(e for c in seed["countries"] if c["country"] == row["country"]
                       for e in c["ethnicities"] if e["name"] == row["ethnicity"])
            _prune_traditions(row["region_slug"], eth, row["sources"])
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ethnicity": row["ethnicity"], "country": row["country"],
                                "region": row["region"], "articles": row["articles"],
                                "unsupported": result["unsupported"]}, ensure_ascii=False) + "\n")
        with open(RESTRUCTURE_LOG, "a", encoding="utf-8") as f:
            for attempt in result["restructure_attempts"]:
                f.write(json.dumps({**attempt, "file": str(out_path.relative_to(REPO_ROOT))},
                                   ensure_ascii=False) + "\n")
        if result["unsupported"]:
            print(f"  ! still unsupported after retry (review by hand): {'; '.join(result['unsupported'])}")
        if result["restructure_problems"]:
            print(f"  ! short rewrite failed, kept long version: {'; '.join(result['restructure_problems'][:4])}")
        print(f"[import] {row['region']} / {row['country']} / {row['ethnicity']}")
        imported += 1
    print(f"imported {imported}, skipped {skipped}")


def _prune_traditions(region_slug: str, eth: dict, sources: str) -> None:
    """Keep only seed traditions the sources mention. The seed is LLM-drafted
    and its traditions show as chips on the culture panel; Asmat's carried
    invented "wuramon", "otsj" and "bipane" (2026-09-27)."""
    kept = [t for t in eth.get("traditions") or [] if not unsupported(sources, f"*{t}*")]
    dropped = [t for t in eth.get("traditions") or [] if t not in kept]
    if not dropped:
        return
    p = DATA_DIR / "seed" / f"{region_slug}.json"
    seed = json.loads(p.read_text(encoding="utf-8"))
    for c in seed["countries"]:
        for e in c["ethnicities"]:
            if e["name"] == eth["name"]:
                e["traditions"] = kept
    p.write_text(json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8")
    eth["traditions"] = kept
    print(f"  seed traditions not in the sources, removed: {', '.join(dropped)}", flush=True)


def _extra_articles(ethnicity: str, main_title: str | None) -> list[dict]:
    out, seen = [], {main_title}
    for pat in EXTRA_ARTICLES:
        title = pat.format(e=ethnicity)
        try:
            art = wiki_fetch_article(title)
        except Exception:
            continue
        if art.get("full_text") and art.get("title") not in seen:
            seen.add(art.get("title"))
            out.append(art)
    return out


def _load_grounding(region: str, country: str, ethnicity: str) -> tuple[dict | None, list[dict] | None]:
    """Read the media sidecar (if it exists) and return (wiki_dict, ich_list).
    Returns (None, None) when no sidecar — writeup then runs ungrounded."""
    sidecar = MEDIA_DIR / slugify(region) / f"{slugify(country)}__{slugify(ethnicity)}.json"
    if not sidecar.exists():
        return None, None
    b = json.loads(sidecar.read_text(encoding="utf-8"))
    srcs = b.get("sources") or {}
    return srcs.get("wikipedia"), srcs.get("unesco_ich")


def load_seed(region_slug: str) -> dict:
    p = DATA_DIR / "seed" / f"{region_slug}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def writeup_path(region: str, country: str, ethnicity: str) -> Path:
    return CONTENT_DIR / slugify(region) / f"{slugify(country)}__{slugify(ethnicity)}.md"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region", nargs="?", help="Region slug (defaults: all under data/seed/)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing writeups")
    ap.add_argument("--only", help="Only generate for this ethnicity (case-insensitive substring)")
    batch = ap.add_mutually_exclusive_group()
    batch.add_argument("--export-batch", metavar="NAME", help="Gather sources and append model prompts to a cloud batch")
    batch.add_argument("--import-batch", metavar="NAME", help="Apply completed cloud results and audit logs")
    args = ap.parse_args()

    if args.import_batch:
        import_batch(args.import_batch, args.force)
        return

    if args.region:
        regions = [args.region]
    else:
        regions = [p.stem for p in (DATA_DIR / "seed").glob("*.json")]

    if args.export_batch:
        export_path = batch_path(BATCH_DIR, args.export_batch)
        export_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        CONTENT_DIR.mkdir(exist_ok=True)

    failures = []
    for region_slug in regions:
        seed = load_seed(region_slug)
        region = seed["region"]
        needle = (args.only or "").lower()

        for country_entry in seed["countries"]:
            country = country_entry["country"]
            for eth in country_entry["ethnicities"]:
                ethnicity = eth["name"]
                if needle and needle not in ethnicity.lower():
                    continue
                out_path = writeup_path(region, country, ethnicity)
                if out_path.exists() and not args.force:
                    print(f"[skip] {region} / {country} / {ethnicity} — already exists")
                    continue
                wiki, ich = _load_grounding(region, country, ethnicity)
                extra = _extra_articles(ethnicity, (wiki or {}).get("title"))
                museum = museum_records_text(region, country, ethnicity)
                mode = "grounded" if (wiki or ich or museum != "(none)") else "ungrounded"
                print(f"[gen ] {region} / {country} / {ethnicity} ({mode}; {1 if wiki else 0}+{len(extra)} "
                      f"Wikipedia articles, {museum.count(chr(10)) + 1 if museum != '(none)' else 0} museum records) ...",
                      flush=True)
                sources = grounding_sources_text(wiki, ich, extra, museum)
                if args.export_batch:
                    row = {"id": f"{region_slug}|{country}|{ethnicity}", "region_slug": region_slug,
                           "region": region, "country": country, "ethnicity": ethnicity, "mode": mode,
                           "prompt": build_writeup_prompt(country, ethnicity, region, eth["traditions"],
                                                            wiki, ich, extra, museum),
                           "sources": sources,
                           "articles": [a.get("title") for a in [wiki, *extra] if a]}
                    with open(export_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    print(f"  -> exported {row['id']} to {export_path.relative_to(REPO_ROOT)}", flush=True)
                    continue
                try:
                    md = generate_writeup(country, ethnicity, region, eth["traditions"], wiki=wiki, ich=ich,
                                          extra_wiki=extra, museum=museum)
                    probs = unsupported(sources, md) if mode == "grounded" else []
                    if probs:
                        print(f"  audit: {len(probs)} unsupported, retrying: {'; '.join(probs[:8])}", flush=True)
                        md = generate_writeup(country, ethnicity, region, eth["traditions"], wiki=wiki, ich=ich,
                                              extra_wiki=extra, museum=museum, feedback=probs)
                        probs = unsupported(sources, md)
                except Exception as e:
                    print(f"  ! failed: {e}", flush=True)
                    failures.append(f"{region} / {country} / {ethnicity}")
                    continue
                with open(AUDIT_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ethnicity": ethnicity, "country": country, "region": region,
                                        "articles": [a.get("title") for a in [wiki, *extra] if a],
                                        "unsupported": probs}, ensure_ascii=False) + "\n")
                if probs:
                    print(f"  ! still unsupported after retry (review by hand): {'; '.join(probs)}", flush=True)
                if mode == "grounded":
                    _prune_traditions(region_slug, eth, sources)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(md, encoding="utf-8")
                # restructure_writeups.py shortens from .long.md when it exists; a
                # stale one would replace this fresh draft (Tlingit, 2026-09-28).
                out_path.with_suffix(".long.md").unlink(missing_ok=True)
                print(f"  -> wrote {out_path.relative_to(REPO_ROOT)}  ({len(md)} chars)", flush=True)
    if failures:
        raise SystemExit(f"Writeup generation failed for {len(failures)} cultures: {', '.join(failures)}")


if __name__ == "__main__":
    main()
