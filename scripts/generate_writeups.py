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
    python scripts/generate_writeups.py --stubs --only Vepsians   # unreviewed stub cultures, Wikipedia only
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


STUB_TITLES = DATA_DIR / "world" / "stub_wiki_titles.json"
# A thin article leaves most sections saying only "The sources used do not
# document Savakot textile ..." (savakot, 2026-10-05: 11 of 14 sections). A
# trailing "but they do not name Y" clause is cut; a sentence that is only such
# a source-gap remark is dropped.
_GAP = r"(?:do|does|did) not (?:otherwise |further |directly )?(?:describe|mention|specify|say|give|record|document|identify|name|provide|detail|discuss|cover|indicate|explain|list|state|include|connect|contain|supply|establish)"
_SRC = r"(?:[Ss]ources?(?: [a-z]+){0,2}?|[Tt]hey|[Ii]t)(?: also| therefore| otherwise)?"
_GAP_CLAUSE = re.compile(r",? (?:but|although|yet|though)(?: the [a-z ]{0,25}sources?(?: [a-z]+){0,2}?| they| it| this)?(?: also)? " + _GAP + r"[^.]*\.")
_NOT_COVERED = re.compile(r"(?:^|(?<=[.!?*”’])[ \t]+)[^.\n]*\b" + _SRC + " " + _GAP + r"[^.\n]*\.", re.M)

# The same remark in other words: "The sources used provide no account of
# Khinalug folktales", "No annual festival calendar ... is documented in the
# sources used" (stub run 2026-10-05).
_NO_ACCOUNT = re.compile(
    r"(?:^|(?<=[.!?*”’])[ \t]+)(?:[^.\n]*\b(?:provides?|gives?|offers?|contains?) no "
    r"(?:account|information|description|details?|record)\b[^.\n]*"
    r"|No [^.\n]* (?:is|are) (?:documented|described|recorded|mentioned|named) in the [a-z ]*sources?[^.\n]*)\.", re.M)


_MIXED = re.compile(r",? (?:but|although|though|yet|however|while)\b|;")


def _drop_uncovered(md: str, keep_mixed: bool = False) -> str:
    """Remove "the sources do not cover X" sentences, then every section left
    empty, then a "## Material culture" with no subsection left.

    `keep_mixed` (the vetted writeups): a sentence that also carries content
    ("The sources do not document X, but they describe Y") is kept whole, and a
    following sentence that began with "They" now says "The sources"."""
    md = _GAP_CLAUSE.sub(".", md)
    if keep_mixed:
        def cut(m: re.Match) -> str:
            return m.group(0) if _MIXED.search(m.group(0)) else "\x00"
        md = _NOT_COVERED.sub(cut, md)
        md = _NO_ACCOUNT.sub(cut, md)
        md = re.sub(r"\x00(\s*)They (?:do |also )?", r"\1The sources ", md)
        md = re.sub(r"\x00(\s*)([A-Z][a-z]* )they\b", r"\1\2the sources", md)   # "What they do document"
        md = md.replace("\x00", "")
    else:
        md = _NOT_COVERED.sub("", md)
        md = _NO_ACCOUNT.sub("", md)
    # The same remark as a reading-list entry: "- Museum catalogue records: no
    # object records were provided in the sources used."
    md = re.sub(r"(?m)^[ \t]*[-*•](?=[^\n]*\bsources? used\b)(?=[^\n]*\b(?:no|not)\b)[^\n]*$\n?", "", md)
    md = re.sub(r"(?m)^ +(?=[A-Z])", "", md)   # a paragraph whose first sentence went
    # List items and bold lead-ins ("**Motifs.**") the removal left empty.
    md = re.sub(r"(?m)^[ \t]*(?:[-*•]|\d+\.)[ \t]*(?:\*\*[^*\n]*\*\*)?[ \t]*$\n?", "", md)
    md = re.sub(r"(?m)^[ \t]*\*\*[^*\n]*\*\*[ \t]*$\n?", "", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    parts = re.split(r"(?m)^(?=#{2,3} )", md)
    head, sections = parts[0], parts[1:]
    kept = [s for s in sections if s.split("\n", 1)[1:] and s.split("\n", 1)[1].strip()
            or s.startswith("## Material culture")]
    out = []
    for i, s in enumerate(kept):
        nxt = kept[i + 1] if i + 1 < len(kept) else ""
        if s.startswith("## Material culture") and not s.split("\n", 1)[1].strip() and not nxt.startswith("### "):
            continue
        out.append(s.rstrip() + "\n\n")
    return (head + "".join(out)).rstrip() + "\n"


def _stub_titles(stubs: list[dict]) -> dict[str, str | None]:
    """English Wikipedia title per stub people (Q-id), from Wikidata sitelinks; cached."""
    import httpx
    cache = json.loads(STUB_TITLES.read_text(encoding="utf-8")) if STUB_TITLES.exists() else {}
    todo = sorted({s["people_key"] for s in stubs} - set(cache))
    for n in range(0, len(todo), 50):
        part = todo[n:n + 50]
        r = httpx.get("https://www.wikidata.org/w/api.php", timeout=60,
                      headers={"User-Agent": "folk-patterns/0.1 (https://folk-patterns.vercel.app)"},
                      params={"action": "wbgetentities", "ids": "|".join(part), "props": "sitelinks",
                              "sitefilter": "enwiki", "format": "json"}).json()
        for q in part:
            cache[q] = (((r.get("entities") or {}).get(q) or {}).get("sitelinks") or {}).get("enwiki", {}).get("title")
    STUB_TITLES.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    return cache


def run_stubs(only: str, limit: int, force: bool, workers: int = 3) -> None:
    """Writeups for the unreviewed stub cultures (data/unvetted/stubs.json),
    grounded only in Wikipedia: the people's own article (Wikidata sitelink)
    plus the related ones. Their museum objects are text-matched and
    unreviewed, so they are not a source. No article, no writeup: nothing is
    written from memory."""
    stubs = json.loads((DATA_DIR / "unvetted" / "stubs.json").read_text(encoding="utf-8"))
    titles = _stub_titles(stubs)
    needle = (only or "").lower()
    todo = [s for s in stubs if (not needle or needle in s["ethnicity"].lower())
            and (force or not writeup_path(s["region"], s["country"], s["ethnicity"]).exists())]
    if limit:
        todo = todo[:limit]
    print(f"stubs: {len(todo)} to write", flush=True)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(lambda s: _stub_writeup(s, titles), todo))


def _stub_writeup(s: dict, titles: dict) -> None:
    region, country, ethnicity = s["region"], s["country"], s["ethnicity"]
    out_path = writeup_path(region, country, ethnicity)
    title = titles.get(s["people_key"])
    wiki = None
    if title:
        try:
            wiki = wiki_fetch_article(title)
        except Exception as e:
            print(f"[skip] {ethnicity}: Wikipedia fetch failed ({e})", flush=True)
            return
    extra = _extra_articles(ethnicity, (wiki or {}).get("title"))
    if not (wiki and wiki.get("full_text")) and not extra:
        print(f"[skip] {country} / {ethnicity}: no Wikipedia article", flush=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ethnicity": ethnicity, "country": country, "region": region,
                                "stub": True, "skipped": "no Wikipedia article"}, ensure_ascii=False) + "\n")
        return
    print(f"[gen ] {country} / {ethnicity} (stub; {1 if wiki else 0}+{len(extra)} Wikipedia articles, "
          f"{len((wiki or {}).get('full_text') or '')} chars main) ...", flush=True)
    sources = grounding_sources_text(wiki, None, extra, "(none)")
    try:
        md = generate_writeup(country, ethnicity, region, [], wiki=wiki, ich=None, extra_wiki=extra)
        probs = unsupported(sources, md)
        if probs:
            print(f"  audit: {len(probs)} unsupported, retrying: {'; '.join(probs[:8])}", flush=True)
            md = generate_writeup(country, ethnicity, region, [], wiki=wiki, ich=None, extra_wiki=extra,
                                  feedback=probs)
            probs = unsupported(sources, md)
    except Exception as e:
        print(f"  ! failed: {e}", flush=True)
        return
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ethnicity": ethnicity, "country": country, "region": region, "stub": True,
                            "articles": [a.get("title") for a in [wiki, *extra] if a],
                            "unsupported": probs}, ensure_ascii=False) + "\n")
    if probs:
        print(f"  ! still unsupported after retry (review by hand): {'; '.join(probs)}", flush=True)
    md = _drop_uncovered(md)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    out_path.with_suffix(".long.md").unlink(missing_ok=True)
    print(f"  -> wrote {out_path.relative_to(REPO_ROOT)}  ({len(md)} chars)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stubs", action="store_true", help="Unreviewed stub cultures instead of the seeds (Wikipedia only)")
    ap.add_argument("--limit", type=int, default=0, help="With --stubs: stop after this many writeups")
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
    if args.stubs:
        run_stubs(args.only, args.limit, args.force)
        return

    if args.region:
        regions = [args.region]
    else:
        regions = [p.stem for p in (DATA_DIR / "seed").glob("*.json")]

    if args.export_batch:
        export_path = batch_path(BATCH_DIR, args.export_batch)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        # A rerun resumes: rows already exported are skipped, and a last line cut
        # off by a crash (the laptop bugchecked mid-export, 2026-10-01) is dropped.
        exported, kept_lines = set(), []
        if export_path.exists():
            for line in export_path.read_text(encoding="utf-8").splitlines():
                try:
                    exported.add(json.loads(line)["id"])
                    kept_lines.append(line)
                except (json.JSONDecodeError, KeyError):
                    pass
            export_path.write_text("".join(l + "\n" for l in kept_lines), encoding="utf-8")
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
                if args.export_batch and f"{region_slug}|{country}|{ethnicity}" in exported:
                    continue
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
