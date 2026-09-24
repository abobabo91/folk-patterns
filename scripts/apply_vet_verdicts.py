"""Write cloud vetting verdicts into the library.

    python scripts/apply_vet_verdicts.py data/vet_verdicts/pilot.jsonl --dry-run
    python scripts/apply_vet_verdicts.py data/vet_verdicts/pilot.jsonl

Each line carries the judge's raw reply (see scripts/cloud_vet_batch.py). It is
parsed with vet_images.parse_reply and persisted with vet_images.apply_verdict —
the same code the local vetter runs — and appended to data/vet_transcript.jsonl.
Download failures are recorded as vision_vetted=None with a note, exactly like
a local download failure, so the next run retries them.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# stdout is re-wrapped as line-buffered UTF-8 by the vet_images import.

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import vet_images as v  # noqa: E402

BY = "cloud-subagent:sonnet"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("verdicts", help="data/vet_verdicts/<batch>.jsonl")
    ap.add_argument("--dry-run", action="store_true", help="parse and tally, write nothing")
    args = ap.parse_args()

    lines = [json.loads(l) for l in Path(args.verdicts).read_text(encoding="utf-8").splitlines() if l.strip()]
    by_meta: dict[str, list[dict]] = {}
    for x in lines:
        by_meta.setdefault(x["meta"], []).append(x)

    tally: Counter = Counter()
    unparsed = []
    for meta_rel, items in by_meta.items():
        meta_path = ROOT / meta_rel
        recs = json.loads(meta_path.read_text(encoding="utf-8"))
        index = {r.get("id"): r for r in recs}
        for x in items:
            rec = index.get(x["id"])
            if rec is None:
                tally["record-missing"] += 1
                continue
            cul = rec.setdefault("cultural", {})
            if "error" in x:
                cul["vision_vetted"] = None
                cul["vision_note"] = x["error"][:200]
                tally["download-failed"] += 1
                continue
            authentic, art_form, reason, confidence, image, era = v.parse_reply(x["reply"])
            if authentic is None:
                unparsed.append(x["id"])
            result = {"authentic": authentic, "art_form": art_form, "reason": reason,
                      "confidence": confidence, "image": image, "era": era}
            tally[{True: "kept", False: "dropped", None: "unparsed"}[authentic]] += 1
            if image == "":
                tally["no-image-field"] += 1
            if args.dry_run:
                continue
            af_before = cul.get("art_form")
            v.apply_verdict(cul, result, BY)
            v._log_transcript({
                "id": x["id"], "source": (rec.get("source") or {}).get("museum", "?"),
                "ethnicity": cul.get("ethnicity"),
                "title": str((rec.get("physical") or {}).get("title") or x["id"])[:120],
                "art_form_before": af_before, "art_form_after": art_form,
                "belongs": authentic, "confidence": confidence, "image": image, "era": era,
                "reason": reason, "by": BY,
            })
        if not args.dry_run:
            meta_path.write_text(json.dumps(recs, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{len(lines)} lines{' (DRY RUN — nothing written)' if args.dry_run else ''}: {dict(tally)}")
    if unparsed:
        print(f"replies the parser could not read ({len(unparsed)}): {unparsed[:10]}")


if __name__ == "__main__":
    main()
