"""Vetting inventory — what has been judged, by what, and what still needs it.

Read-only diagnostic. Run it any time to get the true state rather than
trusting a number written in a doc.

    python scripts/_vet_status.py
    python scripts/_vet_status.py --by-ethnicity
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "library"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--by-ethnicity", action="store_true",
                    help="Per-ethnicity coverage table instead of the summary.")
    args = ap.parse_args()

    total = 0
    state = Counter()          # kept / dropped / failed / never
    with_reason = 0
    by_source = defaultdict(Counter)
    by_eth = defaultdict(Counter)
    af_changed = 0

    for md in LIB.rglob("metadata.json"):
        try:
            records = json.loads(md.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in records:
            total += 1
            cul = r.get("cultural") or {}
            src = (r.get("source") or {}).get("museum") or "?"
            eth = cul.get("ethnicity") or "?"
            v = cul.get("vision_vetted")
            if v is True:
                bucket = "kept"
            elif v is False:
                bucket = "dropped"
            elif "vision_vetted" in cul:
                bucket = "failed"      # attempted, no verdict (quota/timeout)
            else:
                bucket = "never"
            state[bucket] += 1
            by_source[src][bucket] += 1
            by_eth[eth][bucket] += 1
            if cul.get("vision_reason"):
                with_reason += 1
            if cul.get("art_form_vision") and cul["art_form_vision"] != cul.get("art_form"):
                af_changed += 1

    if args.by_ethnicity:
        print(f"{'ethnicity':<26}{'total':>7}{'judged':>8}{'kept':>7}{'dropped':>9}{'todo':>7}")
        for eth in sorted(by_eth, key=lambda e: -sum(by_eth[e].values())):
            c = by_eth[eth]
            n = sum(c.values())
            judged = c["kept"] + c["dropped"]
            todo = c["failed"] + c["never"]
            print(f"{eth[:25]:<26}{n:>7}{judged:>8}{c['kept']:>7}{c['dropped']:>9}{todo:>7}")
        return

    judged = state["kept"] + state["dropped"]
    todo = state["failed"] + state["never"]
    print("=" * 62)
    print(" VETTING STATUS")
    print("=" * 62)
    print(f"  records in library        {total:>6}")
    print(f"  judged (real verdict)     {judged:>6}   {100*judged/total:.0f}%")
    print(f"      kept                  {state['kept']:>6}")
    print(f"      dropped               {state['dropped']:>6}"
          f"   ({100*state['dropped']/judged:.0f}% of judged)" if judged else "")
    print(f"  still to judge            {todo:>6}   {100*todo/total:.0f}%")
    print(f"      never attempted       {state['never']:>6}")
    print(f"      attempted, failed     {state['failed']:>6}   (quota/timeout; retried automatically)")
    print()
    print(f"  art_form corrections      {af_changed:>6}")
    print()
    print(f"  verdicts carrying REASONING {with_reason:>4}   <- current-prompt verdicts")
    if with_reason < judged:
        print(f"  verdicts WITHOUT reasoning  {judged - with_reason:>4}   <- predate the current")
        print(f"{'':30}prompt, need a --force re-vet")
    print()
    print(f"{'source':<16}{'total':>7}{'judged':>8}{'kept':>7}{'dropped':>9}{'todo':>7}")
    for src in sorted(by_source, key=lambda s: -sum(by_source[s].values())):
        c = by_source[src]
        n = sum(c.values())
        print(f"{src:<16}{n:>7}{c['kept']+c['dropped']:>8}{c['kept']:>7}"
              f"{c['dropped']:>9}{c['failed']+c['never']:>7}")


if __name__ == "__main__":
    main()
