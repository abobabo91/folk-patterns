"""Review pending user contributions from R2 `pending/` prefix.

Walks each pending submission, downloads the image + metadata, opens the
image in the default viewer, prints the metadata, and prompts:
    [a] approve — move image + record into library, delete pending
    [r] reject  — delete pending, log why
    [s] skip    — leave in pending, decide later
    [e] edit    — fix a field (title, tradition, art_form, ...) before approving
    [q] quit

Approved records land in the library at the standard path and appear on
the next `python scripts/build_index.py` run.

Usage:
    python scripts/review_contributions.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import LIBRARY_DIR
from folk_patterns.r2 import client as r2_client, get_config
from slugify import slugify

BUCKET = "folk-patterns"
PENDING_PREFIX = "pending/"
REJECT_LOG = Path(__file__).resolve().parents[1] / "data" / "contributions_reject.log"


def list_pending(s3) -> list[str]:
    """Return metadata JSON keys (pending/<id>.json)."""
    out: list[str] = []
    token: str | None = None
    while True:
        kw: dict = {"Bucket": BUCKET, "Prefix": PENDING_PREFIX}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        for obj in r.get("Contents") or []:
            if obj["Key"].endswith(".json"):
                out.append(obj["Key"])
        token = r.get("NextContinuationToken")
        if not token:
            break
    return sorted(out)


def download_to(s3, key: str, dst: Path):
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    dst.write_bytes(body)


def open_image(path: Path):
    """Open image in default viewer. Non-blocking on Windows."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        print(f"  (couldn't auto-open image: {e}). Path: {path}")


def load_ethnicity_index() -> dict[str, dict]:
    """Read data/index.json + shards to know region/country/ethnicity per key."""
    root = Path(__file__).resolve().parents[1] / "data"
    idx = json.loads((root / "index.json").read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for key in idx["ethnicity_keys"]:
        shard = json.loads((root / "ethnicities" / f"{key}.json").read_text(encoding="utf-8"))
        out[key] = {"region": shard["region"], "country": shard["country"], "ethnicity": shard["ethnicity"]}
    return out


def approve(s3, meta: dict, image_bytes: bytes, ethnicity_map: dict) -> None:
    """Move image + record into the library, delete pending copies."""
    key = meta["ethnicity_key"]
    if key not in ethnicity_map:
        print(f"  ! ethnicity_key {key!r} not in seed — aborting approval")
        return
    e = ethnicity_map[key]
    region, country, ethnicity = e["region"], e["country"], e["ethnicity"]
    art_form = meta.get("art_form") or "unclassified"
    tradition = meta.get("tradition") or ethnicity

    dest_dir = (LIBRARY_DIR
                / slugify(region) / slugify(country) / slugify(ethnicity)
                / slugify(art_form) / slugify(tradition))
    (dest_dir / "images").mkdir(parents=True, exist_ok=True)

    # Image filename mirrors the contribution id so we can trace it back
    contrib_id = meta["id"]
    ext = "png" if meta.get("image_mime") == "image/png" else "jpg"
    img_name = f"user_{contrib_id}.{ext}"
    img_dst = dest_dir / "images" / img_name
    img_dst.write_bytes(image_bytes)

    # Also upload the image to R2 at the library path so the site (which
    # serves images from R2) can find it. Same key layout as scrape output.
    lib_key = str(img_dst.relative_to(LIBRARY_DIR)).replace("\\", "/")
    s3.put_object(
        Bucket=BUCKET, Key=lib_key, Body=image_bytes,
        ContentType=meta.get("image_mime") or "image/jpeg",
    )

    # Build a canonical record matching the schema in src/folk_patterns/schema.py
    from folk_patterns.schema import _empty_record
    rec = _empty_record("user-contribution", contrib_id)
    rec["cultural"].update({
        "region": region, "country": country, "ethnicity": ethnicity,
        "tradition": tradition, "art_form": art_form, "pattern_density": 1,
    })
    rec["physical"]["title"] = meta.get("title")
    rec["physical"]["summary"] = meta.get("description")
    rec["source"]["museum_name"] = "User contribution"
    rec["source"]["object_url"] = meta.get("source_url")
    rec["source"]["credit_line"] = meta.get("credit") or meta.get("submitter_name")
    rec["source"]["rights"] = meta.get("license") or "unknown"
    rec["images"].append({
        "url": None, "iiif_id": None, "iiif_base": None, "role": "primary",
        "sha256": None, "bytes": len(image_bytes),
        "local_path": str(img_dst.relative_to(LIBRARY_DIR.parent)).replace("\\", "/"),
    })
    rec["raw"] = {"contribution_meta": meta}

    # Append to metadata.json
    meta_path = dest_dir / "metadata.json"
    if meta_path.exists():
        recs = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        recs = []
    recs.append(rec)
    meta_path.write_text(json.dumps(recs, indent=2, ensure_ascii=False), encoding="utf-8")

    # Delete pending
    for pending_key in (meta["image_key"], f"pending/{contrib_id}.json"):
        try:
            s3.delete_object(Bucket=BUCKET, Key=pending_key)
        except Exception:
            pass

    print(f"  APPROVED. Wrote to {img_dst}")
    print("  Run `python scripts/build_index.py` (or a full rebuild) to surface this on the site.")


def reject(s3, meta: dict, reason: str) -> None:
    """Delete pending copies and log the rejection."""
    REJECT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with REJECT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{meta['id']}\t{meta.get('ethnicity_key','?')}\t{reason}\n")
    for pending_key in (meta["image_key"], f"pending/{meta['id']}.json"):
        try:
            s3.delete_object(Bucket=BUCKET, Key=pending_key)
        except Exception:
            pass
    print(f"  REJECTED. Logged to {REJECT_LOG.name}: {reason}")


def prompt_edit(meta: dict) -> dict:
    """Let the reviewer tweak fields before approving."""
    editable = ["title", "description", "tradition", "art_form", "credit", "license"]
    for f in editable:
        current = meta.get(f) or ""
        new = input(f"  {f} [{current}]: ").strip()
        if new:
            meta[f] = new
    return meta


def main():
    cfg = get_config()
    s3 = r2_client()
    ethnicity_map = load_ethnicity_index()
    keys = list_pending(s3)
    print(f"{len(keys)} pending contribution(s).\n")
    if not keys:
        return

    tmpdir = Path(tempfile.mkdtemp(prefix="folk-review-"))
    try:
        for meta_key in keys:
            meta = json.loads(s3.get_object(Bucket=BUCKET, Key=meta_key)["Body"].read())
            print("=" * 72)
            print(f"id             : {meta['id']}")
            print(f"submitted_at   : {meta['submitted_at']}")
            print(f"ethnicity_key  : {meta['ethnicity_key']}")
            print(f"art_form       : {meta.get('art_form')}")
            print(f"tradition      : {meta.get('tradition')}")
            print(f"title          : {meta.get('title')}")
            print(f"description    : {(meta.get('description') or '')[:200]}")
            print(f"source_url     : {meta.get('source_url')}")
            print(f"credit         : {meta.get('credit')}")
            print(f"license        : {meta.get('license')}")
            print(f"submitter      : {meta.get('submitter_name')} <{meta.get('submitter_email')}>")

            # Download + open image
            img_key = meta["image_key"]
            local_img = tmpdir / img_key.split("/")[-1]
            download_to(s3, img_key, local_img)
            open_image(local_img)
            print(f"(image at {local_img})")

            while True:
                choice = input("[a]pprove / [r]eject / [s]kip / [e]dit / [q]uit > ").strip().lower()
                if choice == "a":
                    approve(s3, meta, local_img.read_bytes(), ethnicity_map)
                    break
                if choice == "r":
                    reason = input("  reason: ").strip() or "no reason given"
                    reject(s3, meta, reason)
                    break
                if choice == "s":
                    print("  skipped.")
                    break
                if choice == "e":
                    meta = prompt_edit(meta)
                    continue
                if choice == "q":
                    print("bye")
                    return
                print("  ? unrecognized choice")
    finally:
        # cleanup temp images
        try:
            for f in tmpdir.iterdir():
                f.unlink()
            tmpdir.rmdir()
        except Exception:
            pass


if __name__ == "__main__":
    main()
