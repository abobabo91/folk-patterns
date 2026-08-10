"""Sync library/**.jpg to R2 bucket. Idempotent — skips objects that already exist.

Usage:
    python scripts/upload_to_r2.py                # dry-run: list what would upload
    python scripts/upload_to_r2.py --commit       # actually upload
    python scripts/upload_to_r2.py --commit -j 8  # parallel with 8 workers
    python scripts/upload_to_r2.py --commit --force  # re-upload even if exists

Keys in R2 mirror the on-disk relative path under library/:
    library/central-asia/uzbekistan/uzbek/textile/suzani/images/va_O360718.jpg
      -> R2 key: central-asia/uzbekistan/uzbek/textile/suzani/images/va_O360718.jpg
"""
from __future__ import annotations

import argparse
import io
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import LIBRARY_DIR
from folk_patterns.r2 import client as r2_client, get_config


def existing_keys(bucket: str, s3) -> set[str]:
    out: set[str] = set()
    token: str | None = None
    while True:
        kw: dict = {"Bucket": bucket}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        for obj in r.get("Contents") or []:
            out.add(obj["Key"])
        token = r.get("NextContinuationToken")
        if not token:
            break
    return out


def collect_files() -> list[tuple[Path, str]]:
    """Return [(local_path, r2_key)]."""
    files: list[tuple[Path, str]] = []
    for p in LIBRARY_DIR.rglob("*.jpg"):
        rel = p.relative_to(LIBRARY_DIR).as_posix()
        files.append((p, rel))
    return files


def upload_one(s3, bucket: str, path: Path, key: str) -> tuple[str, int]:
    with open(path, "rb") as f:
        s3.upload_fileobj(
            f, bucket, key,
            ExtraArgs={"ContentType": "image/jpeg", "CacheControl": "public, max-age=31536000, immutable"},
        )
    return key, path.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="Actually upload; without this flag we dry-run")
    ap.add_argument("--force", action="store_true", help="Re-upload even if key exists")
    ap.add_argument("-j", "--jobs", type=int, default=8, help="Parallel upload workers")
    args = ap.parse_args()

    cfg = get_config()
    bucket = cfg["bucket"]
    print(f"Bucket: {bucket}")
    print(f"Endpoint: {cfg['endpoint']}")
    print(f"Public base: {cfg['public_base']}")

    files = collect_files()
    print(f"Local files to consider: {len(files)}")

    s3 = r2_client()
    if not args.force:
        existing = existing_keys(bucket, s3)
        print(f"Already in R2: {len(existing)}")
        files = [(p, k) for (p, k) in files if k not in existing]

    total_bytes = sum(p.stat().st_size for p, _ in files)
    print(f"To upload: {len(files)} files, {total_bytes / 1e6:.1f} MB")

    if not args.commit:
        print("\n(dry-run — pass --commit to actually upload)")
        for p, k in files[:5]:
            print(f"  would upload: {k}  ({p.stat().st_size / 1024:.0f} KB)")
        if len(files) > 5:
            print(f"  ... and {len(files) - 5} more")
        return

    if not files:
        print("Nothing to upload.")
        return

    done = 0
    done_bytes = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = {ex.submit(upload_one, s3, bucket, p, k): (p, k) for (p, k) in files}
        for fut in as_completed(futures):
            try:
                key, size = fut.result()
                done += 1
                done_bytes += size
                if done % 25 == 0 or done == len(files):
                    print(f"  {done}/{len(files)}  ({done_bytes / 1e6:.1f} / {total_bytes / 1e6:.1f} MB)", flush=True)
            except Exception as e:
                p, k = futures[fut]
                print(f"  ! failed {k}: {e}", flush=True)

    print(f"\nUploaded {done}/{len(files)}. Sample public URL:")
    if files:
        _, sample_key = files[0]
        print(f"  {cfg['public_base'].rstrip('/')}/{sample_key}")


if __name__ == "__main__":
    main()
