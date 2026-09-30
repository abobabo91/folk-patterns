"""Run exported writeup prompts in a Claude Code cloud session, without local sources.

    FOLK_LLM_BACKEND=claude python scripts/cloud_writeup_batch.py w001 20

Each run answers at most LIMIT pending cultures (default 20), audits the long
draft, retries it once if grounded, then audits and retries the short rewrite.
Results and rewrite attempt logs are flushed to data/writeup_results/NAME.jsonl
after each culture. Re-running skips completed IDs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from folk_patterns.writeup import build_writeup_prompt, restructure_writeup, run_claude, unsupported  # noqa: E402
from restructure_writeups import _meta, audit  # noqa: E402


def batch_path(folder: str, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError("Batch name must contain only letters, digits, hyphens or underscores")
    return ROOT / "data" / folder / f"{name}.jsonl"


def run(name: str, limit: int = 20) -> None:
    if limit < 1:
        raise ValueError("LIMIT must be a positive integer")
    os.environ.setdefault("FOLK_LLM_BACKEND", "claude")
    batch = batch_path("writeup_batches", name)
    results = batch_path("writeup_results", name)
    rows = {r["id"]: r for r in (json.loads(line) for line in
            batch.read_text(encoding="utf-8").splitlines() if line.strip())}
    done = set()
    if results.exists():
        done = {json.loads(line)["id"] for line in results.read_text(encoding="utf-8").splitlines()
                if line.strip()}
    pending = [r for key, r in rows.items() if key not in done]
    results.parent.mkdir(parents=True, exist_ok=True)
    with open(results, "a", encoding="utf-8") as f:
        for row in pending[:limit]:
            md = run_claude(row["prompt"])
            probs = unsupported(row["sources"], md) if row["mode"] == "grounded" else []
            if probs:
                retry = build_writeup_prompt(row["country"], row["ethnicity"], row["region"], [],
                                             feedback=probs, initial_prompt=row["prompt"])
                md = run_claude(retry)
                probs = unsupported(row["sources"], md)
            eth, country = _meta(md)
            attempts = []
            rewrite = ""
            rewrite_problems = []
            for attempt in (1, 2):
                rewrite, event = restructure_writeup(md, eth, country, feedback=rewrite_problems or None)
                rewrite_problems = audit(md, rewrite)
                attempts.append({"attempt": attempt, "chars_before": len(md), "chars_after": len(rewrite),
                                 "cost_usd": event.get("total_cost_usd"),
                                 "seconds": (event.get("duration_ms") or 0) / 1000,
                                 "problems": rewrite_problems})
                if not rewrite_problems:
                    break
            result = {"id": row["id"], "long_markdown": md,
                      "short_markdown": rewrite if not rewrite_problems else None,
                      "unsupported": probs, "restructure_problems": rewrite_problems,
                      "restructure_attempts": attempts}
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()
            done.add(row["id"])
            print(f"[{len(done)}/{len(rows)}] {row['id']}: long audit {len(probs)}, "
                  f"short audit {len(rewrite_problems)}", flush=True)
    print(f"pending {len(rows) - len(done)}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="Exported batch name")
    ap.add_argument("limit", nargs="?", type=int, default=20, metavar="LIMIT",
                    help="Maximum pending cultures this run (default: 20)")
    args = ap.parse_args()
    run(args.name, args.limit)
