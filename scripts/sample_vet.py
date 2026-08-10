"""Sample-vetter: pick N random records from a single source, vision-check each
via Claude Haiku CLI, and report an aggregate pass rate.

Use this to decide "trust this source wholesale" vs "vet every record" —
saves LLM calls compared to running the full vetter against every image
in every source we add.

Usage:
  python scripts/sample_vet.py cleveland --n 50
  python scripts/sample_vet.py commons_arch --n 50
"""
from __future__ import annotations

import argparse
import io
import json
import random
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import LIBRARY_DIR

MODEL = "claude-haiku-4-5-20251001"

PROMPT = """Read the image at path {path}.

Context: this image is currently attributed to the {ethnicity} people from
{country}, stored as a "{current_af}".

Question: is this image an authentic representation of {ethnicity} material
folk culture, craft, dress, ritual, everyday life, festival, or architecture
(or a very closely related group in {country})?

Reply EXACTLY one word: YES or NO."""


def _first_local_path(rec):
    for img in rec.get("images", []):
        lp = img.get("local_path")
        if not lp:
            continue
        p = Path(lp)
        if not p.is_absolute():
            p = LIBRARY_DIR.parent / p
        if p.exists():
            return p
    return None


def _ask(image_path, ethnicity, country, current_af):
    prompt = PROMPT.format(path=str(image_path), ethnicity=ethnicity,
                           country=country, current_af=current_af)
    try:
        res = subprocess.run(
            f'claude --print --dangerously-skip-permissions --no-session-persistence '
            f'--tools Read --model {MODEL}',
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=90, shell=True,
        )
        if res.returncode != 0:
            return None
        out = (res.stdout or "").strip().upper()
        for tok in reversed(out.replace("\n"," ").split()):
            t = tok.strip(".,!?:;()[]").upper()
            if t in ("YES", "NO"):
                return t == "YES"
        return None
    except subprocess.TimeoutExpired:
        return None


def _iter_source(source):
    """Yield (record, meta_path) for every library record of the given source."""
    for m in sorted(LIBRARY_DIR.glob("*/*/*/*/*/metadata.json")):
        recs = json.loads(m.read_text(encoding="utf-8"))
        for r in recs:
            if (r.get("source") or {}).get("museum") == source:
                yield r, m


def _vet_one(rec):
    cul = rec.get("cultural") or {}
    ip = _first_local_path(rec)
    if not ip:
        return None, rec
    verdict = _ask(ip, cul.get("ethnicity",""), cul.get("country",""), cul.get("art_form","unclassified"))
    return verdict, rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="museum source (cleveland, commons_arch, europeana, va, smithsonian, met)")
    ap.add_argument("--n", type=int, default=50, help="sample size")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    all_recs = [r for r, _ in _iter_source(args.source)]
    if not all_recs:
        print(f"No records for source '{args.source}'"); return
    random.seed(42)
    sample = random.sample(all_recs, min(args.n, len(all_recs)))
    print(f"Sampling {len(sample)}/{len(all_recs)} records from '{args.source}'\n")

    yes = 0; no = 0; err = 0
    rejected = []; accepted_sample = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_vet_one, r): r for r in sample}
        for f in as_completed(futs):
            v, rec = f.result()
            cul = rec.get("cultural") or {}
            phys = rec.get("physical") or {}
            title = phys.get("title") or rec.get("id") or "?"
            eth = cul.get("ethnicity","")
            if v is True:
                yes += 1
                mark = "✓"
                if len(accepted_sample) < 5:
                    accepted_sample.append(f"  ✓ [{eth}] {str(title)[:70]}")
            elif v is False:
                no += 1
                mark = "✗"
                rejected.append(f"  ✗ [{eth}] {str(title)[:70]}")
            else:
                err += 1
                mark = "?"
            print(f"{mark} [{eth[:12]:12}] {str(title)[:60]}", flush=True)

    total = yes + no
    pct = (yes / total * 100) if total else 0
    print(f"\n== SOURCE '{args.source}' — sample of {len(sample)} ==")
    print(f"  ✓ {yes}   ✗ {no}   ? {err}   pass rate {pct:.0f}%")
    if rejected:
        print(f"\nRejected examples (up to 15):")
        for r in rejected[:15]:
            print(r)
    if accepted_sample:
        print(f"\nAccepted examples (5):")
        for r in accepted_sample:
            print(r)


if __name__ == "__main__":
    main()
