"""Export library records as a vetting batch for a cloud session.

    python scripts/export_vet_batch.py --name pilot --ids-file ids.json
    python scripts/export_vet_batch.py --name b001 --todo --limit 500 --seed 1

Writes data/vet_batches/<name>.jsonl — one line per record with the exact
prompt the local vetter would send (vet_images.build_prompt, pointing at
work/img/<key>.jpg) and the image URLs to fetch it from. The cloud session
needs nothing else: not the library, not a key. See docs/cloud-vetting.md.

`--todo` picks records without a current-prompt verdict (no vision_image yet);
`--ids-file` takes a JSON list of record ids. R2 is listed first when the
object is already uploaded (same bytes as the local file), then the source
museum's URL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

# stdout is re-wrapped as line-buffered UTF-8 by the vet_images import.

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import vet_images as v  # noqa: E402
from folk_patterns.util import LIBRARY_DIR  # noqa: E402

OUT_DIR = ROOT / "data" / "vet_batches"


def record_key(meta: str, rec_id: str) -> str:
    """Filesystem-safe name for one record copy's work files. Keyed on the
    metadata file as well as the id: the same museum object can be filed
    under two ethnicities, and each copy gets its own verdict."""
    return hashlib.sha1(f"{meta}|{rec_id}".encode("utf-8")).hexdigest()[:16]


def _r2_keys() -> tuple[set[str], str | None]:
    try:
        from folk_patterns.r2 import client, get_config
        cfg = get_config()
        s3 = client()
        keys: set[str] = set()
        token = None
        while True:
            kw = {"Bucket": cfg["bucket"]}
            if token:
                kw["ContinuationToken"] = token
            r = s3.list_objects_v2(**kw)
            keys.update(o["Key"] for o in r.get("Contents") or [])
            token = r.get("NextContinuationToken")
            if not token:
                return keys, cfg.get("public_base")
    except Exception as e:  # no creds / offline: museum URLs still work
        print(f"  (R2 listing skipped: {e})")
        return set(), None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="batch name, e.g. pilot or b001")
    ap.add_argument("--ids-file", help="JSON list of record ids to export")
    ap.add_argument("--todo", action="store_true",
                    help="records without a current-prompt verdict (no vision_image)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--exclude-batches", action="store_true",
                    help="skip records already present in any data/vet_batches/*.jsonl")
    args = ap.parse_args()
    if not (args.ids_file or args.todo):
        raise SystemExit("pass --ids-file or --todo")

    wanted = set(json.loads(Path(args.ids_file).read_text(encoding="utf-8"))) if args.ids_file else None
    exported: set[str] = set()
    if args.exclude_batches:
        for p in OUT_DIR.glob("*.jsonl"):
            for line in p.read_text(encoding="utf-8").splitlines():
                exported.add(json.loads(line)["key"])

    rows = []
    seen: set[str] = set()
    for meta_path, rec in v._iter_library_records():
        rid = rec.get("id")
        meta_rel = meta_path.relative_to(ROOT).as_posix()
        key = record_key(meta_rel, rid or "")
        if not rid or key in seen or key in exported:
            continue
        cul = rec.get("cultural") or {}
        if wanted is not None and rid not in wanted:
            continue
        if args.todo and cul.get("vision_image"):
            continue
        seen.add(key)
        rows.append((meta_path, rec))

    if args.limit and len(rows) > args.limit:
        random.Random(args.seed).shuffle(rows)
        rows = rows[:args.limit]

    r2_keys, r2_base = _r2_keys()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{args.name}.jsonl"
    n_r2 = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for meta_path, rec in rows:
            cul = rec.get("cultural") or {}
            key = record_key(meta_path.relative_to(ROOT).as_posix(), rec["id"])
            img_path = f"work/img/{key}.jpg"
            title, desc, place = v._record_text(rec)
            urls = []
            img = (rec.get("images") or [{}])[0]
            lp = img.get("local_path")
            if lp and r2_base:
                r2_key = Path(lp).as_posix().split("library/", 1)[-1]
                if r2_key in r2_keys:
                    urls.append(f"{r2_base.rstrip('/')}/{r2_key}")
                    n_r2 += 1
            if img.get("url"):
                urls.append(img["url"])
            out.write(json.dumps({
                "id": rec["id"],
                "key": key,
                "meta": meta_path.relative_to(ROOT).as_posix(),
                "image_path": img_path,
                "urls": urls,
                "prompt": v.build_prompt(img_path, cul.get("ethnicity") or "",
                                         cul.get("country") or "",
                                         cul.get("art_form") or "unclassified",
                                         title=title, desc=desc, place=place),
            }, ensure_ascii=False) + "\n")
    print(f"wrote {out_path.relative_to(ROOT)}: {len(rows)} records "
          f"({n_r2} with an R2 copy, prompt model-agnostic; judge with {v.MODEL} or its cloud equivalent)")


if __name__ == "__main__":
    main()
