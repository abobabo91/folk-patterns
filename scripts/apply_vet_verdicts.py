"""Write cloud vetting verdicts into library metadata or Commons sidecars.

    python scripts/apply_vet_verdicts.py data/vet_verdicts/pilot.jsonl --dry-run
    python scripts/apply_vet_verdicts.py data/vet_verdicts/pilot.jsonl

Each line carries the judge's raw reply (see scripts/cloud_vet_batch.py). It is
parsed with vet_images.parse_reply. Library rows use vet_images.apply_verdict
and are appended to data/vet_transcript.jsonl; download failures set
vision_vetted=None with a note. Commons rows follow the local sidecar field
rules; changed photo URLs are skipped, and download failures leave vetted absent.
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
from export_vet_batch import commons_id  # noqa: E402

BY = "cloud-subagent:sonnet"


def _commons_photo(data: dict, x: dict) -> dict | None:
    """Return the photo only when its sidecar slot still has the exported URL."""
    try:
        prefix, index_text, digest = x["id"].rsplit(":", 2)
        meta = prefix.removeprefix("commons:")
        index = int(index_text)
        photos = (data.get("sources") or {}).get("commons") or []
        photo = photos[index] if index >= 0 else None
        url = photo.get("thumb_url") or photo.get("full_url") or ""
        if (prefix.startswith("commons:") and meta == x["meta"]
                and len(digest) == 40 and commons_id(meta, index, url) == x["id"]):
            return photo
    except (KeyError, ValueError, IndexError, AttributeError, TypeError):
        pass
    return None


def _apply_commons(items: list[dict], meta_path: Path, dry_run: bool,
                   tally: Counter, unparsed: list[str]) -> None:
    if not meta_path.is_file():
        tally["sidecar-missing"] += len(items)
        return
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    changed = False
    for x in items:
        photo = _commons_photo(data, x)
        if photo is None:
            tally["url-mismatch-or-photo-missing"] += 1
            print(f"  skipped changed Commons photo: {x['id']}")
            continue
        if "error" in x:
            tally["download-failed"] += 1
            if not dry_run:
                if x.get("force"):
                    photo.pop("editorial_reviewed", None)
                    photo.pop("editorial_reviewer", None)
                photo.pop("vetted", None)
                photo["vetted_note"] = x["error"]
                changed = True
            continue
        authentic, art_form, reason, confidence, image, era = v.parse_reply(x["reply"])
        if authentic is None:
            unparsed.append(x["id"])
        tally[{True: "kept", False: "dropped", None: "unparsed"}[authentic]] += 1
        if image == "":
            tally["no-image-field"] += 1
        if dry_run:
            continue
        if x.get("force"):
            photo.pop("editorial_reviewed", None)
            photo.pop("editorial_reviewer", None)
        if authentic is None:
            photo.pop("vetted", None)
        else:
            photo["vetted"] = authentic
        if art_form and art_form in v.VALID_ART_FORMS:
            photo["vetted_art_form"] = art_form
        photo["vetted_by"] = BY
        photo["vetted_image"] = image or ""
        photo["vetted_era"] = era or ""
        changed = True
    if changed:
        meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


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
        if any(x["id"].startswith("commons:") for x in items):
            if not (meta_rel.startswith("content/media/") and meta_rel.endswith(".json")
                    and ".." not in Path(meta_rel).parts
                    and all(x["id"].startswith("commons:") for x in items)):
                tally["invalid-commons-path"] += len(items)
                continue
            _apply_commons(items, meta_path, args.dry_run, tally, unparsed)
            continue
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
