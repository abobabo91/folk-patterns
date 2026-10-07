"""Delete the image copies the site no longer serves: R2 objects and library/ images.

    python scripts/prune_images.py            # dry run: counts and sizes only
    python scripts/prune_images.py --commit   # delete

The site loads most museum images from the museum itself (build_index.py,
HOTLINK_HOSTS). The images it still serves from R2 are the R2 URLs written into
data/objects, data/ethnicities and data/index.json by the last build_index.py
run; every other R2 object and every other file under library/**/images/ is
deleted. Run build_index.py first, deploy, and check the site before --commit.
library/**/metadata.json and work/ are never touched. The duplicate-picture
check keeps working without the files: build_index.py reads the features of
deleted images from data/image_features.json.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from folk_patterns.r2 import client as r2_client, get_config  # noqa: E402
from upload_to_r2 import existing_keys  # noqa: E402


def referenced_keys(base: str) -> set[str]:
    pat = re.compile(re.escape(base) + r"/([^\"\s]+)")
    keys: set[str] = set()
    files = [ROOT / "data" / "index.json", *(ROOT / "data" / "objects").glob("*.json"),
             *(ROOT / "data" / "ethnicities").glob("*.json")]
    for f in files:
        keys.update(pat.findall(f.read_text(encoding="utf-8")))
    return keys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    cfg = get_config()
    base = cfg["public_base"].rstrip("/")
    keep = referenced_keys(base)
    s3 = r2_client()
    in_r2 = existing_keys(cfg["bucket"], s3)
    missing = keep - in_r2
    print(f"site uses {len(keep)} R2 images; {len(missing)} of them are not in the bucket")
    if missing:
        print("  e.g.", sorted(missing)[:3])
        sys.exit("refusing to prune: the site references R2 keys that do not exist")
    drop_r2 = sorted(in_r2 - keep)
    lib = ROOT / "library"
    local = [p for p in lib.rglob("images/*") if p.is_file()]
    drop_local = [p for p in local if p.relative_to(lib).as_posix() not in keep]
    size = sum(p.stat().st_size for p in drop_local)
    print(f"R2: {len(in_r2)} objects, delete {len(drop_r2)}, keep {len(in_r2) - len(drop_r2)}")
    print(f"library: {len(local)} image files, delete {len(drop_local)} ({size / 1e9:.2f} GB), keep {len(local) - len(drop_local)}")
    if not args.commit:
        print("dry run; --commit deletes")
        return
    for i in range(0, len(drop_r2), 1000):
        chunk = drop_r2[i:i + 1000]
        r = s3.delete_objects(Bucket=cfg["bucket"], Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True})
        if r.get("Errors"):
            print("  R2 errors:", r["Errors"][:3])
        print(f"  R2 deleted {min(i + 1000, len(drop_r2))}/{len(drop_r2)}")
    for p in drop_local:
        p.unlink()
    print(f"library: deleted {len(drop_local)} files")


if __name__ == "__main__":
    main()
