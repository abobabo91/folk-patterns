"""Audit a culture's profiles against the sources the writer was given.

    python scripts/audit_profile.py --region north_america --only Hopi          # report
    python scripts/audit_profile.py --region north_america --only Hopi --fix    # and repair the short one

Sources are the same as generate_writeups.py: the sidecar's Wikipedia article
and UNESCO list, related Wikipedia articles and the museum catalogue text.
Italic terms and numbers found in none of them are reported for both the short
(.md) and long (.long.md) profile. --fix removes unsupported terms from the
short profile only: a "(*term*)" tag after an English name is dropped, and a
glossary line for the term is deleted. The long profile is never edited; its
leftovers are for a person to read. Needed because the long draft can use a
word without italics, which its audit does not see, and the short rewrite then
italicises it (Hopi "manta", 2026-09-28).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import generate_writeups as gw  # noqa: E402
from folk_patterns.writeup import grounding_sources_text, museum_records_text, unsupported  # noqa: E402


def sources_for(region: str, country: str, ethnicity: str) -> str:
    wiki, ich = gw._load_grounding(region, country, ethnicity)
    extra = gw._extra_articles(ethnicity, (wiki or {}).get("title"))
    return grounding_sources_text(wiki, ich, extra, museum_records_text(region, country, ethnicity))


def fix_short(md: str, terms: list[str]) -> str:
    for t in terms:
        esc = re.escape(t)
        md = re.sub(rf"\s*\(\*{esc}\*\)", "", md)                      # "**Dress** (*manta*)" -> "**Dress**"
        md = re.sub(rf"^- \*{esc}\*\s+—.*(?:\n|$)", "", md, flags=re.M)  # glossary line
        md = re.sub(rf"\*{esc}\*", t, md)                               # any other italic use: plain
    return md


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", required=True, help="seed slug, e.g. north_america")
    ap.add_argument("--only", required=True, help="ethnicity name")
    ap.add_argument("--fix", action="store_true")
    a = ap.parse_args()
    seed = gw.load_seed(a.region)
    for c in seed["countries"]:
        for e in c["ethnicities"]:
            if e["name"].lower() != a.only.lower():
                continue
            src = sources_for(seed["region"], c["country"], e["name"])
            short = gw.writeup_path(seed["region"], c["country"], e["name"])
            for p in (short.with_suffix(".long.md"), short):
                if not p.exists():
                    continue
                probs = unsupported(src, p.read_text(encoding="utf-8"))
                print(f"{p.name}: {len(probs)} unsupported" + (": " + "; ".join(probs) if probs else ""))
                if a.fix and p == short and probs:
                    terms = [re.match(r"term not in the sources: (.*) \(", x).group(1)
                             for x in probs if x.startswith("term not")]
                    p.write_text(fix_short(p.read_text(encoding="utf-8"), terms), encoding="utf-8")
                    left = unsupported(src, p.read_text(encoding="utf-8"))
                    print(f"  fixed {len(terms)} terms; left: {'; '.join(left) or 'none'}")
            return
    raise SystemExit(f"{a.only} not in data/seed/{a.region}.json")


if __name__ == "__main__":
    main()
