"""Second, editorial review of model-accepted Wikimedia Commons photos.

The first Commons pass in :mod:`vet_images` answers whether an image appears
to show folk culture.  This pass checks the source caption and category as
well, because a broad Commons category can still supply an image of another
people or only a country-level subject.  It reviews up to twelve cached or
downloaded photos on one contact sheet, then stores ``editorial_reviewed``
and the review reason on each photo that received a verdict.

Run with ``--dry-run`` to create sheets and transcripts without changing
sidecars.  The script uses the subscription-backed Codex CLI through
``folk_patterns.codex_cli.ask``; it does not use a paid inference API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = ROOT / "content" / "media"
COMMONS_REVIEW_DIR = ROOT / "work" / "commons-review"
EDITORIAL_DIR = ROOT / "work" / "commons-editorial"
TRANSCRIPT = EDITORIAL_DIR / "transcript.jsonl"
MAX_PHOTOS_PER_SHEET = 12
TILE_WIDTH = 360
TILE_IMAGE_HEIGHT = 328
TILE_LABEL_HEIGHT = 36
SHEET_COLUMNS = 3

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image, ImageDraw, ImageFont, ImageOps  # noqa: E402

from folk_patterns.codex_cli import ask  # noqa: E402
from vet_images import _download  # noqa: E402


REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "idx": {"type": "integer"},
                    "pass": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["idx", "pass", "reason"],
            },
        },
    },
    "required": ["reviews"],
}

EDITORIAL_CRITERIA = """PASS only when both conditions hold:
1. The caption or source category ties the photo specifically to the named
   people, not only to a country or region.
2. The image visibly shows that people's culture: dress, adornment, music,
   dance, festival, architecture, craft, or objects attributed to them.

FAIL a country- or region-level attribution only; another people; a generic
landscape, city, or food image without a people link; digital art or modern
graphics; maps; flags only; portraits of named modern individuals with no
cultural content; unrelated items; or a caption and image that disagree.
When unsure, FAIL."""


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _reviewer() -> str:
    return f"codex-editorial-{date.today().isoformat()}"


def _cache_path(sidecar: Path, idx: int, url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return COMMONS_REVIEW_DIR / sidecar.stem / f"{idx:02}-{digest}.jpg"


def _load_cached_or_download(url: str, dst: Path) -> Image.Image | None:
    """Return a detached RGB image, using the vetter's polite downloader."""
    if not dst.exists():
        if not url:
            return None
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not _download(url, dst):
            dst.unlink(missing_ok=True)
            return None
    try:
        with Image.open(dst) as image:
            image.load()
            return image.convert("RGB")
    except (OSError, ValueError):
        return None


def _font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, 24)
        except OSError:
            pass
    return ImageFont.load_default()


def _contact_sheet(tiles: list[tuple[int, Image.Image]], dst: Path) -> None:
    """Write a readable three-column sheet labelled with source indices."""
    rows = (len(tiles) + SHEET_COLUMNS - 1) // SHEET_COLUMNS
    tile_height = TILE_LABEL_HEIGHT + TILE_IMAGE_HEIGHT
    sheet = Image.new("RGB", (SHEET_COLUMNS * TILE_WIDTH, rows * tile_height), "white")
    draw = ImageDraw.Draw(sheet)
    font = _font()
    for pos, (idx, image) in enumerate(tiles):
        x = (pos % SHEET_COLUMNS) * TILE_WIDTH
        y = (pos // SHEET_COLUMNS) * tile_height
        draw.rectangle((x, y, x + TILE_WIDTH - 1, y + TILE_LABEL_HEIGHT - 1), fill="#202124")
        draw.text((x + 10, y + 5), f"#{idx}", fill="white", font=font)
        thumb = ImageOps.contain(image, (TILE_WIDTH - 8, TILE_IMAGE_HEIGHT - 8))
        image_x = x + (TILE_WIDTH - thumb.width) // 2
        image_y = y + TILE_LABEL_HEIGHT + (TILE_IMAGE_HEIGHT - thumb.height) // 2
        sheet.paste(thumb, (image_x, image_y))
        image.close()
    dst.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dst, format="JPEG", quality=92, optimize=True)
    sheet.close()


def build_prompt(ethnicity: str, country: str,
                 captions: list[tuple[int, dict]]) -> str:
    """Build the numbered caption block sent alongside a contact sheet."""
    lines = []
    for idx, photo in captions:
        lines.append(
            f"#{idx}\n"
            f"title: {photo.get('title') or '(none)'}\n"
            f"description: {photo.get('description') or '(none)'}\n"
            f"source_category: {photo.get('source_category') or '(none)'}"
        )
    return (
        "Review the numbered Wikimedia Commons photos in the attached contact "
        "sheet for the ethnographic collection. The culture is "
        f"{ethnicity or '(unspecified)'} and the country is {country or '(unspecified)'}.\n\n"
        "The number printed on each tile matches the caption block below. "
        "Judge the visible image together with its title, description, and "
        "source category.\n\n"
        f"{EDITORIAL_CRITERIA}\n\n"
        "Return JSON matching the supplied schema. Include one review for "
        "every numbered tile, using the tile's exact integer index. Give a "
        "short, specific reason. If a tile is missing from your reply, it "
        "will remain unreviewed.\n\n"
        "NUMBERED CAPTIONS:\n" + "\n\n".join(lines)
    )


def _parse_reviews(raw: str, indices: set[int]) -> dict[int, tuple[bool, str]]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        return {}
    reviews: dict[int, tuple[bool, str]] = {}
    for item in data["reviews"]:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        passed = item.get("pass")
        reason = item.get("reason")
        if (type(idx) is not int or idx not in indices or
                type(passed) is not bool or not isinstance(reason, str)):
            continue
        if idx not in reviews:
            reviews[idx] = (passed, reason.strip())
    return reviews


def _log_reply(sidecar: Path, sheet: Path, indices: list[int], raw: str,
               error: str | None = None) -> None:
    TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "sidecar": _relative(sidecar),
        "sheet": _relative(sheet),
        "indices": indices,
        "raw_reply": raw,
    }
    if error:
        entry["error"] = error
    with TRANSCRIPT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def process_sidecar(sidecar: Path, *, dry_run: bool = False) -> Counter:
    """Review one sidecar and return this run's passed/failed/unreviewed tally."""
    bundle = json.loads(sidecar.read_text(encoding="utf-8"))
    photos = (bundle.get("sources") or {}).get("commons") or []
    targets = [
        (idx, photo) for idx, photo in enumerate(photos)
        if photo.get("vetted") is True and "editorial_reviewed" not in photo
    ]
    tally: Counter = Counter()
    reviewer = _reviewer()
    for batch_no in range(0, len(targets), MAX_PHOTOS_PER_SHEET):
        batch = targets[batch_no:batch_no + MAX_PHOTOS_PER_SHEET]
        tiles: list[tuple[int, Image.Image]] = []
        caption_rows: list[tuple[int, dict]] = []
        for idx, photo in batch:
            url = photo.get("thumb_url") or photo.get("full_url") or ""
            image = _load_cached_or_download(url, _cache_path(sidecar, idx, url))
            if image is None:
                tally["unreviewed"] += 1
                continue
            tiles.append((idx, image))
            caption_rows.append((idx, photo))

        if not tiles:
            continue
        sheet = EDITORIAL_DIR / sidecar.stem / f"sheet-{batch_no // MAX_PHOTOS_PER_SHEET + 1:03}.jpg"
        _contact_sheet(tiles, sheet)
        indices = [idx for idx, _ in tiles]
        raw = ""
        try:
            raw = ask(
                build_prompt(bundle.get("ethnicity") or "", bundle.get("country") or "",
                             caption_rows),
                image=sheet.read_bytes(),
                schema=REVIEW_SCHEMA,
            )
            if not isinstance(raw, str):
                raw = json.dumps(raw, ensure_ascii=False)
            reviews = _parse_reviews(raw, set(indices))
            _log_reply(sidecar, sheet, indices, raw)
        except Exception as exc:  # leave every tile unreviewed after a failed call
            _log_reply(sidecar, sheet, indices, raw, str(exc))
            reviews = {}

        tally["unreviewed"] += len(indices) - len(reviews)
        for idx, (passed, reason) in reviews.items():
            if not dry_run:
                photos[idx]["editorial_reviewed"] = passed
                photos[idx]["editorial_reviewer"] = reviewer
                photos[idx]["editorial_reason"] = reason
            tally["passed" if passed else "failed"] += 1

    if not dry_run and targets:
        sidecar.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    return tally


def _selected(path: Path, needles: list[str]) -> bool:
    if not needles:
        return True
    stem = path.stem.lower()
    return any(needle.lower() in stem or needle.lower().replace(" ", "-") in stem
               for needle in needles)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", default=[], metavar="NAME",
                        help="Only sidecars whose filename contains one of these names")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process at most N selected sidecars (0 means all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Create sheets/transcripts but do not write sidecars")
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit must be non-negative")

    sidecars = [p for p in sorted(MEDIA_DIR.rglob("*.json")) if _selected(p, args.only)]
    if args.limit:
        sidecars = sidecars[:args.limit]
    for sidecar in sidecars:
        try:
            tally = process_sidecar(sidecar, dry_run=args.dry_run)
            print(f"[commons-editorial] {sidecar.stem}: "
                  f"passed {tally['passed']} / failed {tally['failed']} / "
                  f"unreviewed {tally['unreviewed']}", flush=True)
        except (OSError, ValueError, TypeError) as exc:
            print(f"[commons-editorial] {sidecar.stem}: passed 0 / failed 0 / "
                  f"unreviewed 0 (error: {exc})", flush=True)


if __name__ == "__main__":
    main()
