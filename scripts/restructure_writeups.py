"""Rewrite existing culture writeups into the fixed short format
(folk_patterns.writeup.RESTRUCTURE_PROMPT): an "At a glance" table, one lead
sentence + at most 5 bullets per section, a glossary. Uses only the facts
already in the writeup.

    python scripts/restructure_writeups.py --only Yoruba Kazakh --preview   # -> work/writeup-preview/
    python scripts/restructure_writeups.py --only Yoruba                      # overwrite content/…md
    python scripts/restructure_writeups.py                                    # every writeup

The original is kept next to it as <name>.long.md the first time it is
overwritten. Each rewrite is audited against its original before it is
written — every heading present, every vernacular term and every number of
the new text found in the old one — and retried once when it fails, with the audit's objections added to the prompt; a
second failure leaves the file untouched and is reported. Cost and audit go
to data/writeup_restructure.jsonl.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.writeup import missing_headings, restructure_writeup  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CONTENT = REPO / "content"
LOG = REPO / "data" / "writeup_restructure.jsonl"
PREVIEW = REPO / "work" / "writeup-preview"


def _meta(md: str) -> tuple[str, str]:
    t = re.search(r'^title:\s*"?(.*?)"?\s*$', md, re.M)
    s = re.search(r'^subtitle:\s*"?(.*?)"?\s*$', md, re.M)
    return (t.group(1) if t else ""), (s.group(1) if s else "")


_STOP = {"and", "the", "with", "for", "from", "its", "our"}


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def audit(original: str, new: str) -> list[str]:
    """Problems with a rewrite: missing headings, vernacular terms or numbers
    the original does not contain (a rewrite may drop, never add)."""
    if not new:
        return ["no usable reply"]
    probs = [f"missing {h}" for h in missing_headings(new)]
    body = new.split("## Sources")[0]
    o = _fold(original)
    terms = set(re.findall(r"\(\*([^*()]+)\*\)", body)) | set(re.findall(r"^- \*([^*]+)\*\s—", body, re.M))
    # word by word: "Hanafi madhhab" is fine when both words are in the original
    # apart; "Beshir halı" is not when "halı" is nowhere in it (2026-09-25).
    # English words in a term pass on their stem ("Aladura churches" vs "church");
    # short words must match exactly, so an added vernacular word still fails.
    def ok(w: str) -> bool:
        f = _fold(w)
        return f in _STOP or f in o or (len(f) >= 6 and f[:-2] in o)
    for t in terms:
        bad = [w for w in re.findall(r"[^\W\d_][^\W_]*", t) if len(w) > 2 and not ok(w)]
        if bad:
            probs.append(f"term not in original: {t} ({', '.join(bad)})")
    for n in set(re.findall(r"\b\d[\d,.]*\b", body)):
        if n.strip(",.") not in original:
            probs.append(f"number not in original: {n}")
    return probs


def run(path: Path, preview: bool) -> str:
    long = path.with_suffix(".long.md")
    md = (long if long.exists() else path).read_text(encoding="utf-8")   # always rewrite from the long original
    eth, country = _meta(md)
    cost, probs, attempt, new = 0.0, [], 0, ""
    for attempt in (1, 2):
        new, ev = restructure_writeup(md, eth, country, feedback=probs or None)
        cost += ev.get("total_cost_usd") or 0
        probs = audit(md, new)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"file": str(path.relative_to(REPO)), "attempt": attempt, "chars_before": len(md),
                                "chars_after": len(new), "cost_usd": ev.get("total_cost_usd"),
                                "seconds": (ev.get("duration_ms") or 0) / 1000, "problems": probs},
                               ensure_ascii=False) + "\n")
        if not probs:
            break
    if probs:
        return f"{eth}: FAILED after 2 attempts, left untouched — {probs[:4]}"
    if preview:
        PREVIEW.mkdir(parents=True, exist_ok=True)
        (PREVIEW / path.name).write_text(new + "\n", encoding="utf-8")
    else:
        long = path.with_suffix(".long.md")
        if not long.exists():
            long.write_text(md, encoding="utf-8")
        path.write_text(new + "\n", encoding="utf-8")
    return f"{eth}: {len(md)} -> {len(new)} chars, ${cost:.3f}" + (" (2nd attempt)" if attempt == 2 else "")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    files = [p for p in sorted(CONTENT.glob("*/*.md")) if not p.name.endswith(".long.md")]
    if a.only:
        files = [p for p in files if any(_meta(p.read_text(encoding="utf-8"))[0].lower() == o.lower() for o in a.only)]
    print(f"{len(files)} writeups")
    with ThreadPoolExecutor(a.workers) as ex:
        for line in ex.map(lambda p: run(p, a.preview), files):
            print(line)
