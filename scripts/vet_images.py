"""Unified agentic vetter for every image in the library.

For each museum object and each Commons documentary photo, ask Claude Haiku
(via CLI + Read tool) two things at once:

  1. Does this image genuinely depict authentic material folk culture of the
     tagged ethnicity? YES / NO.
  2. If YES, which art-form bucket does it belong to? one of:
     textile, garment, ceramic, architectural, jewelry, metalwork,
     painting-mss, sculpture, household, photo.

Persists results back into the source of truth:
  - library/**/metadata.json — sets `cultural.art_form_vision` +
    `cultural.vision_vetted` on the museum record.
  - content/media/**.json — sets `vetted` + `vetted_art_form` on Commons
    photos.

Idempotent — skips items already vetted unless --force.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests

from folk_patterns.util import LIBRARY_DIR

MEDIA_DIR = Path(__file__).resolve().parents[1] / "content" / "media"
UA = "folk-patterns/0.1 (research atlas)"
MODEL = "claude-haiku-4-5-20251001"

VALID_ART_FORMS = {
    "textile", "garment", "ceramic", "architectural", "jewelry",
    "metalwork", "painting-mss", "sculpture", "household", "photo",
    "unclassified",
}

PROMPT = """Read the image at path {path}.

Context: this image is currently attributed to the {ethnicity} people from
{country}, and is stored as a "{current_af}" in our atlas of world folk
culture.

Answer TWO questions on TWO separate lines, nothing else:

Line 1 — AUTHENTIC: does this image depict authentic material folk culture,
craft, dress, ritual, everyday life, festival, or built environment of the
{ethnicity} (or a closely related group in {country})? Reply YES or NO.

  Accept (YES) if it is: any traditional craft or object (textile, garment,
  vessel, tool, weapon, jewelry, mask, puppet), any traditional building or
  domestic interior, any festival / ritual / market scene, a portrait of
  ORDINARY unnamed people in traditional dress, a musician playing a
  traditional instrument, a landscape that shows a distinctively traditional
  built environment (villages, farming, boat, fishing scene).

  Reject (NO) if it is: a linguistic / genetic / population chart, a
  political / heraldic emblem (coat of arms, flag, seal), a portrait of a
  NAMED HISTORICAL RULER or religious figure (kings, sultans, monks by
  name, medieval Persian miniatures of specific individuals — even if
  authentically of that culture), a portrait of a specific modern politician
  or celebrity, a scientific specimen (plant, insect, mineral), a book cover,
  a screenshot, a war photograph, a generic modern cityscape or crowd, a
  logo, or a diagram / map / infographic.

Line 2 — ART_FORM: pick the single best category. Choose from EXACTLY these:
  textile, garment, ceramic, architectural, jewelry, metalwork,
  painting-mss, sculpture, household, photo, unclassified
  Use "photo" only if the image is a documentary photograph of a scene
  (people, festival, market), not of an individual object. Use
  "unclassified" only if the image is authentic but doesn't fit any bucket.

Reply in EXACTLY this format:
AUTHENTIC: <YES or NO>
ART_FORM: <one of the ten valid categories>"""


_LAST_DL = [0.0]


def _download(url: str, dst: Path, min_interval: float = 0.4) -> bool:
    for attempt in range(2):
        wait = min_interval - (time.time() - _LAST_DL[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(
                url,
                headers={
                    "User-Agent": UA,
                    "Referer": "https://commons.wikimedia.org/",
                    "Accept": "image/*,*/*;q=0.8",
                },
                timeout=45, stream=True,
            )
            _LAST_DL[0] = time.time()
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            with open(dst, "wb") as f:
                shutil.copyfileobj(r.raw, f)
            if dst.stat().st_size > 1000:
                return True
        except Exception:
            _LAST_DL[0] = time.time()
            time.sleep(1)
    return False


def _ask_claude(image_path: Path, ethnicity: str, country: str, current_af: str,
                timeout: int = 90) -> tuple[bool | None, str | None]:
    """Return (authentic, art_form). Either can be None on error."""
    prompt = PROMPT.format(
        path=str(image_path), ethnicity=ethnicity, country=country,
        current_af=current_af,
    )
    try:
        res = subprocess.run(
            f'claude --print --dangerously-skip-permissions --no-session-persistence '
            f'--tools Read --model {MODEL}',
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, shell=True,
        )
        if res.returncode != 0:
            return None, None
        out = (res.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return None, None

    authentic: bool | None = None
    art_form: str | None = None
    for line in out.splitlines():
        line_u = line.strip().upper()
        if line_u.startswith("AUTHENTIC:"):
            v = line_u.split(":", 1)[1].strip()
            if "YES" in v:
                authentic = True
            elif "NO" in v:
                authentic = False
        elif line_u.startswith("ART_FORM:"):
            v = line.split(":", 1)[1].strip().lower().split()[0] if ":" in line else ""
            v = v.strip(".,;:")
            if v in VALID_ART_FORMS:
                art_form = v
    # Fallback: last two lines if the format wasn't strict
    if authentic is None:
        toks = [t.strip(".,!?:()[]").upper() for t in out.replace("\n", " ").split()]
        for t in toks:
            if t in ("YES", "NO"):
                authentic = t == "YES"
                break
    return authentic, art_form


# -----------------------------------------------------------------------------
# Library-record vetting
# -----------------------------------------------------------------------------

def _iter_library_records():
    """Yield (meta_path, record) for every object record with at least one image."""
    for meta_path in sorted(LIBRARY_DIR.glob("*/*/*/*/*/metadata.json")):
        try:
            recs = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in recs:
            if not r.get("images"):
                continue
            yield meta_path, r


def _first_local_path(rec: dict) -> Path | None:
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


def _first_remote_url(rec: dict) -> str | None:
    """Fallback for records whose images only have `url` (Europeana records
    saved to R2 don't keep a local_path in metadata)."""
    for img in rec.get("images", []):
        u = img.get("url")
        if u and u.startswith("http"):
            return u
    return None


def _vet_library_record(meta_path: Path, rec: dict, tmp: Path):
    """Return (result_dict, meta_path, rec_id) — updates rec in-place is done by caller."""
    cul = rec.get("cultural") or {}
    ethnicity = cul.get("ethnicity") or ""
    country = cul.get("country") or ""
    current_af = cul.get("art_form") or "unclassified"
    img_path = _first_local_path(rec)
    dl_tmp: Path | None = None
    if not img_path:
        # Try to download from a remote URL (Europeana / R2 records that don't
        # keep local_path in metadata) to a temp file. Delete after vet.
        url = _first_remote_url(rec)
        if not url:
            return {"skip": "no-image-anywhere"}, meta_path, rec.get("id")
        dl_tmp = tmp / f"lib_{abs(hash(rec.get('id') or url))}.jpg"
        if not _download(url, dl_tmp):
            return {"skip": "download-failed"}, meta_path, rec.get("id")
        img_path = dl_tmp
    authentic, art_form = _ask_claude(img_path, ethnicity, country, current_af)
    if dl_tmp is not None:
        try: dl_tmp.unlink()
        except Exception: pass
    return {"authentic": authentic, "art_form": art_form}, meta_path, rec.get("id")


def _vet_library(workers: int, force: bool, only: str | None) -> None:
    """Vet every library object. Persists per-file (batched)."""
    needle = (only or "").lower()
    # Group records by meta_path so we write once per file.
    by_file: dict[Path, list[dict]] = {}
    for meta_path, r in _iter_library_records():
        cul = r.get("cultural") or {}
        eth = (cul.get("ethnicity") or "").lower()
        if needle and needle not in eth:
            continue
        if not force and "vision_vetted" in cul:
            continue
        by_file.setdefault(meta_path, []).append(r)

    total_targets = sum(len(v) for v in by_file.values())
    print(f"[lib] {len(by_file)} files, {total_targets} records to vet")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        done = 0
        for meta_path, recs_to_vet in by_file.items():
            all_recs = json.loads(meta_path.read_text(encoding="utf-8"))
            id_to_rec = {r.get("id"): r for r in all_recs}
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_vet_library_record, meta_path, r, tmp): r for r in recs_to_vet}
                for f in as_completed(futs):
                    result, mp, rec_id = f.result()
                    done += 1
                    target = id_to_rec.get(rec_id)
                    if not target:
                        continue
                    cul = target.setdefault("cultural", {})
                    if result.get("skip"):
                        cul["vision_vetted"] = None
                        cul["vision_note"] = result["skip"]
                        mark = "?"
                    else:
                        auth = result.get("authentic")
                        af = result.get("art_form")
                        cul["vision_vetted"] = bool(auth) if auth is not None else None
                        cul["vision_by"] = "claude-haiku-4-5"
                        if af and af in VALID_ART_FORMS and af != "unclassified":
                            cul["art_form_vision"] = af
                        mark = "✓" if auth else ("✗" if auth is False else "?")
                    title = (target.get("physical") or {}).get("title") or rec_id
                    print(f"  {mark} [{cul.get('ethnicity','?')}] {str(title)[:60]}  ({done}/{total_targets})", flush=True)
            meta_path.write_text(json.dumps(all_recs, indent=2, ensure_ascii=False), encoding="utf-8")


# -----------------------------------------------------------------------------
# Commons-sidecar vetting (re-uses existing sidecar structure)
# -----------------------------------------------------------------------------

def _vet_commons_photo(url: str, ethnicity: str, country: str, tmp: Path):
    ext = ".jpg"
    dst = tmp / f"vet_{abs(hash(url))}{ext}"
    if not _download(url, dst):
        return {"skip": "download-failed"}
    authentic, art_form = _ask_claude(dst, ethnicity, country, "photo")
    try:
        dst.unlink()
    except Exception:
        pass
    return {"authentic": authentic, "art_form": art_form}


def _vet_commons(workers: int, force: bool, only: str | None) -> None:
    needle = (only or "").lower()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for p in sorted(MEDIA_DIR.rglob("*.json")):
            if needle and needle not in p.name.lower():
                continue
            b = json.loads(p.read_text(encoding="utf-8"))
            ethn = b.get("ethnicity") or ""
            country = b.get("country") or ""
            photos = (b.get("sources") or {}).get("commons") or []
            targets = [cp for cp in photos if force or "vetted" not in cp]
            if not targets:
                continue
            print(f"[commons] {p.stem}  targets={len(targets)}", flush=True)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_vet_commons_photo,
                                  cp.get("thumb_url") or cp.get("full_url"),
                                  ethn, country, tmp): cp for cp in targets}
                for f in as_completed(futs):
                    cp = futs[f]
                    result = f.result()
                    if result.get("skip"):
                        cp.pop("vetted", None)   # keep in gallery on failure
                        cp["vetted_note"] = result["skip"]
                        mark = "?"
                    else:
                        auth = result.get("authentic")
                        af = result.get("art_form")
                        if auth is True:
                            cp["vetted"] = True
                        elif auth is False:
                            cp["vetted"] = False
                        else:
                            cp.pop("vetted", None)
                        if af and af in VALID_ART_FORMS:
                            cp["vetted_art_form"] = af
                        cp["vetted_by"] = "claude-haiku-4-5"
                        mark = "✓" if auth else ("✗" if auth is False else "?")
                    title = cp.get("title", "")
                    print(f"  {mark} [{ethn}] {str(title)[:60]}", flush=True)
            p.write_text(json.dumps(b, indent=2, ensure_ascii=False), encoding="utf-8")


def _recheck_rejected(workers: int, only: str | None) -> None:
    """Second-pass over items the first vet marked as `vetted=False`. Re-runs
    with the standard (updated) prompt and reinstates items Claude accepts
    the second time — catches false negatives from overly conservative
    rejections."""
    needle = (only or "").lower()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # Library records with vision_vetted=False.
        by_file: dict[Path, list[dict]] = {}
        for meta_path, r in _iter_library_records():
            cul = r.get("cultural") or {}
            eth = (cul.get("ethnicity") or "").lower()
            if needle and needle not in eth:
                continue
            if cul.get("vision_vetted") is False:
                by_file.setdefault(meta_path, []).append(r)
        total = sum(len(v) for v in by_file.values())
        print(f"[recheck-lib] {total} previously-rejected library records")
        flipped = 0
        for meta_path, recs_to_check in by_file.items():
            all_recs = json.loads(meta_path.read_text(encoding="utf-8"))
            id_to_rec = {r.get("id"): r for r in all_recs}
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_vet_library_record, meta_path, r, tmp): r for r in recs_to_check}
                for f in as_completed(futs):
                    result, mp, rec_id = f.result()
                    target = id_to_rec.get(rec_id)
                    if not target: continue
                    cul = target.setdefault("cultural", {})
                    auth = result.get("authentic")
                    if auth is True:
                        cul["vision_vetted"] = True
                        cul["vision_flipped"] = True
                        flipped += 1
                        af = result.get("art_form")
                        if af and af in VALID_ART_FORMS and af != "unclassified":
                            cul["art_form_vision"] = af
                        print(f"  ✓→ [{cul.get('ethnicity')}] {(target.get('physical') or {}).get('title','')[:60]}", flush=True)
            meta_path.write_text(json.dumps(all_recs, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[recheck-lib] flipped {flipped} of {total} back to accepted")

        # Commons photos with vetted=False.
        cflipped = 0
        for p in sorted(MEDIA_DIR.rglob("*.json")):
            if needle and needle not in p.name.lower():
                continue
            b = json.loads(p.read_text(encoding="utf-8"))
            ethn = b.get("ethnicity") or ""
            country = b.get("country") or ""
            photos = (b.get("sources") or {}).get("commons") or []
            targets = [cp for cp in photos if cp.get("vetted") is False]
            if not targets:
                continue
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_vet_commons_photo,
                                  cp.get("thumb_url") or cp.get("full_url"),
                                  ethn, country, tmp): cp for cp in targets}
                for f in as_completed(futs):
                    cp = futs[f]
                    result = f.result()
                    if result.get("authentic") is True:
                        cp["vetted"] = True
                        cp["vetted_flipped"] = True
                        af = result.get("art_form")
                        if af and af in VALID_ART_FORMS:
                            cp["vetted_art_form"] = af
                        cflipped += 1
                        print(f"  ✓→ [commons/{ethn}] {cp.get('title','')[:60]}", flush=True)
            p.write_text(json.dumps(b, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[recheck-commons] flipped {cflipped} back to accepted")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["library", "commons", "all"], default="all")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", help="Substring filter on ethnicity/sidecar name")
    ap.add_argument("--recheck-rejected", action="store_true",
                    help="Second-pass over vetted=False items with the current (updated) prompt.")
    args = ap.parse_args()

    if args.recheck_rejected:
        _recheck_rejected(args.workers, args.only)
        return

    if args.target in ("commons", "all"):
        _vet_commons(args.workers, args.force, args.only)
    if args.target in ("library", "all"):
        _vet_library(args.workers, args.force, args.only)


if __name__ == "__main__":
    main()
