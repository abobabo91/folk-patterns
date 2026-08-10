"""Agentic vision filter for Commons-fallback photos.

For each ethnicity's Wikimedia Commons photo list, ask Claude (Haiku, via the
CLI) to look at the image and decide whether it depicts authentic material
folk culture of that ethnic group. Reject anything that's a linguistic
diagram, unrelated portrait, modern selfie, generic landscape, coat of arms,
etc.

The vetting decisions are persisted to the media sidecar as a per-photo
`vetted` field (True / False / None) so re-runs are idempotent and cheap.

Time cost: ~34 ethnicities × ~12 photos with 4-8 workers ≈ 15-30 min.
Runs via `claude --print --tools Read` — uses the personal Claude subscription,
no paid API charges.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests

MEDIA_DIR = Path(__file__).resolve().parents[1] / "content" / "media"
UA = "folk-patterns/0.1 (research atlas)"
MODEL = "claude-haiku-4-5-20251001"


PROMPT = """Read the image at path {path}.

Question: does this image depict authentic material folk culture of the
{ethnicity} people from {country} — meaning a traditional textile, garment,
craft object, architecture, ritual scene, or cultural practice?

REJECT if the image is: a linguistic/genetic distribution chart or map, a
modern political rally, a generic landscape or cityscape, a coat of arms or
flag, a portrait of a named individual (politician, athlete, celebrity),
a scientific specimen (plant, insect), a screenshot, a graph, a diagram, or
any other clearly non-folk-culture image.

Reply with EXACTLY one word: YES or NO. Nothing else."""


import time as _time
_LAST_DL_TS = [0.0]


def _download(url: str, dst: Path, min_interval: float = 0.6) -> bool:
    """Download with polite spacing + one retry. Wikimedia soft-throttles fast
    anon fetches; a 0.6s gap + Referer keeps most requests below the threshold."""
    for attempt in range(2):
        wait = min_interval - (_time.time() - _LAST_DL_TS[0])
        if wait > 0:
            _time.sleep(wait)
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
            _LAST_DL_TS[0] = _time.time()
            if r.status_code == 429:
                _time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            with open(dst, "wb") as f:
                shutil.copyfileobj(r.raw, f)
            if dst.stat().st_size > 1000:
                return True
        except Exception:
            _LAST_DL_TS[0] = _time.time()
            _time.sleep(1)
    return False


def _ask_claude(image_path: Path, ethnicity: str, country: str, timeout: int = 90) -> str | None:
    """Return 'YES' / 'NO' / None (on error/ambiguity)."""
    prompt = PROMPT.format(path=str(image_path), ethnicity=ethnicity, country=country)
    try:
        res = subprocess.run(
            f'claude --print --dangerously-skip-permissions --no-session-persistence '
            f'--tools Read --model {MODEL}',
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, shell=True,
        )
        if res.returncode != 0:
            return None
        out = (res.stdout or "").strip().upper()
        # Claude sometimes returns commentary then a final YES/NO. Take the last
        # word that is YES or NO.
        for token in reversed(out.replace("\n", " ").split()):
            t = token.strip(".,!?:;()[]").upper()
            if t in ("YES", "NO"):
                return t
        return None
    except subprocess.TimeoutExpired:
        return None


def _vet_one(idx: int, cp: dict, ethnicity: str, country: str, tmp: Path) -> tuple[int, str | None, str]:
    url = cp.get("thumb_url") or cp.get("full_url")
    if not url:
        return idx, None, "no-url"
    ext = ".jpg"
    dst = tmp / f"vet_{idx}{ext}"
    if not _download(url, dst):
        return idx, None, "download-failed"
    verdict = _ask_claude(dst, ethnicity, country)
    try:
        dst.unlink()
    except Exception:
        pass
    return idx, verdict, cp.get("title", "")[:60]


def vet_sidecar(sidecar_path: Path, workers: int = 4, force: bool = False) -> tuple[int, int]:
    """Vet all commons_photos in one sidecar. Returns (kept, dropped)."""
    b = json.loads(sidecar_path.read_text(encoding="utf-8"))
    country = b.get("country", "")
    ethnicity = b.get("ethnicity", "")
    photos = (b.get("sources") or {}).get("commons") or []
    if not photos:
        return 0, 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {}
            for i, cp in enumerate(photos):
                if not force and "vetted" in cp:
                    continue
                futs[ex.submit(_vet_one, i, cp, ethnicity, country, tmp)] = i
            for f in as_completed(futs):
                idx, verdict, title = f.result()
                # verdict: "YES" -> vetted=True, "NO" -> False, None -> keep as
                # untouched null so the photo isn't dropped just because we
                # couldn't download it for review.
                if verdict == "YES":
                    photos[idx]["vetted"] = True
                elif verdict == "NO":
                    photos[idx]["vetted"] = False
                else:
                    photos[idx].pop("vetted", None)  # remove any stale False
                photos[idx]["vetted_by"] = "claude-haiku-4-5"
                mark = "✓" if verdict == "YES" else ("✗" if verdict == "NO" else "?")
                print(f"  {mark} [{ethnicity}] {title}", flush=True)
    kept = sum(1 for cp in photos if cp.get("vetted"))
    dropped = sum(1 for cp in photos if cp.get("vetted") is False)
    sidecar_path.write_text(json.dumps(b, indent=2, ensure_ascii=False), encoding="utf-8")
    return kept, dropped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Only vet ethnicities whose slug contains this substring")
    ap.add_argument("--workers", type=int, default=4, help="Parallel Claude subprocess workers")
    ap.add_argument("--force", action="store_true", help="Re-vet even photos that already have a `vetted` field")
    args = ap.parse_args()

    needle = (args.only or "").lower()
    total_kept = total_dropped = 0
    for p in sorted(MEDIA_DIR.rglob("*.json")):
        if needle and needle not in p.name.lower():
            continue
        print(f"[vet] {p.stem}", flush=True)
        kept, dropped = vet_sidecar(p, workers=args.workers, force=args.force)
        total_kept += kept
        total_dropped += dropped
        print(f"  -> kept {kept}, dropped {dropped}", flush=True)
    print(f"\nTOTAL: kept {total_kept}, dropped {total_dropped}")


if __name__ == "__main__":
    main()
