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
from collections import Counter
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

PROMPT = """Read the image at path {path}. LOOK AT THE PICTURE FIRST — it is
the primary evidence. The text below is only context, and it is sometimes
wrong. Judge what you can actually see.

WHAT THIS ATLAS IS
We are building a visual atlas of world folk art, organised by ethnic group.
Every entry should be something made or used by ordinary people of a named
ethnicity: woven, dyed and embroidered textiles, everyday and festival
dress, pots, baskets, tools, weapons, jewellery, masks and puppets,
vernacular buildings and their surface ornament, and documentary photographs
of traditional life. What we care about most is PATTERN AND CRAFT — how a
people decorate the things they make.

We DO collect monumental architecture and its ornament. Mosques, temples,
palaces, mausolea, forts and walled old towns are a deliberate part of this
atlas — their tilework, carving and brick patterning are among the richest
surface pattern any culture produces. Do NOT reject a building for being
grand, famous, imperially patronised, religious, or a tourist destination.
Hagia Sophia, Wat Phra Kaew, the Registan and Shah-i-Zinda all belong here.
"Vernacular vs. monumental" is NOT a distinction this atlas makes.

We are NOT building an art-history collection, a museum catalogue, or an
archaeology database. European fine art, portable antiquities dug out of the
ground, portraits of named rulers, and pictures of museums themselves do not
belong here.

THIS RECORD CLAIMS TO BE
  ethnicity : {ethnicity}   ({country})
  category  : {current_af}

THE HOLDING MUSEUM'S METADATA
  title:            {title}
  description:      {desc}
  location on file: {place}

CRITICAL — "location on file" is usually the country of the museum that
HOLDS the object, not where it was made. Ethnographic collections of the
whole world sit in Swedish, Dutch, British and German museums; "Sweden" on
a Malaysian Iban textile just means Gothenburg owns it. A European or
American location is NEVER by itself a reason to reject. The true origin,
when recorded, is usually inside the description, often in Swedish, Dutch
or German.

YOUR TWO JUDGEMENTS

(a) BELONGS — does this picture belong under {ethnicity}?
    Say YES when the picture plainly shows a traditional object, garment,
    textile, building or scene and nothing contradicts it. A plain museum
    photograph of a patterned cloth, robe, pot, basket or tool on a neutral
    background is exactly what we collect. You do not need proof of the
    specific ethnic group — a plausible object from the right cultural
    region is enough, because we cannot tell neighbouring groups apart by
    eye either.

    Religious art MADE BY the culture counts: Ethiopian Orthodox painting
    on hand-woven cotton, Buddha figures, Hindu deities, mosque tilework,
    ritual masks. Christian or Buddhist subject matter is not evidence of
    European origin — judge who made it, not what it depicts.

    A painting or drawing that DOCUMENTS traditional dress or daily life
    counts as YES *when it was made within the culture's own world* — an
    anonymous Ottoman costume-album folio of a dancer's clothing is evidence
    of the costume, so keep it. But a work by a NAMED European master
    (Rubens' costume book, a Grand Tour watercolourist) is European art
    history even when its subject is accurate: say NO. The test is who made
    it and for whom, not how faithful the depiction is.

    ETHNICITY TIE-BREAK. Be decisive when the group is in doubt:
      - the museum's own record NAMES a different people ("Shan cloth" filed
        under Bamar, "Sierra Leone Kusaibi type" filed under Wolof, "Afghan
        war kilim" filed under Kurdish) -> NO, it is mis-filed;
      - the specific group is merely unverifiable, and the object is a
        plausible piece from the right region -> YES. We cannot tell
        neighbouring groups apart by eye and neither can you; absence of
        proof is not evidence of a mistake.

    Say NO only on positive evidence, from the picture or the description:
      - an unrelated culture or subject: European fine art, a named European
        artist, a colonial exhibition, a museum gift shop or gallery
        interior, a modern tourist or holiday snapshot, a generic cityscape;
      - the ethnonym is only an incidental word in a European title ("the
        Persian Sibyl" on a Baroque drawing, "Cham" as an artist's name);
      - a map, distribution chart, diagram, coat of arms, flag, logo,
        screenshot, or a scientific specimen;
      - PORTABLE excavated antiquity: grave goods, cylinder seals,
        predynastic palettes, Bronze-Age burial pottery, sculpture
        fragments recovered from a dig;
      - a portrait of a NAMED ruler, sultan, or celebrity.

    A STANDING building still in the cultural landscape — mosque, fort,
    mausoleum, caravanserai, temple, palace, walled old town — is
    architecture, not archaeology. Say YES even when ruined, even when
    world-famous, even when built by an emperor. Only reject a site when
    nothing stands and the picture is of an excavation or foundations.

    Religious objects still belonging to a living tradition — masks, icons,
    votive figures, painted panels, ritual vessels — are YES regardless of
    how finely made. Craftsmanship is not a disqualification. Judge age and
    context, not quality: an 8th-century temple fragment in a museum is
    archaeology, a carved mask or painted icon from a living practice is
    folk culture.

    PHOTOGRAPHIC STYLE IS NEVER A REASON TO REJECT. If the subject shows
    traditional dress, craft, pattern or building, keep it — whether the
    shot is staged, modern, touristic, a portrait, or taken for a charity
    or news report. Judge the SUBJECT, not the photographer's intent. Only
    reject a photograph when its actual subject is something else (a street
    market where a monument is mere backdrop, a European traveller posing).

(b) ART_FORM — is "{current_af}" the right category, and if not, what is?
    Pick the single best fit for what the picture SHOWS:
      textile        cloth, weaving, embroidery, carpets, felt
      garment        worn clothing, robes, hats, shoes
      ceramic        pottery, tiles, porcelain
      architectural  buildings, ornament, interiors
      jewelry        body ornament
      metalwork      vessels, weapons, tools in metal
      painting-mss   paintings, drawings, manuscripts, prints
      sculpture      carving, statuary, masks
      household      baskets, furniture, domestic tools
      photo          documentary photo of a SCENE (people, festival, market)
      unclassified   authentic but fits nothing above

REPLY IN EXACTLY THIS FORMAT, reasoning first:
REASON: <one or two sentences. Say what the picture ACTUALLY SHOWS, then
  why it does or does not fit {ethnicity}. Describe what you see, not what
  the title claims.>
BELONGS: <YES or NO>
ART_FORM: <one category from the list>
CONFIDENCE: <HIGH, MEDIUM or LOW>"""


TRANSCRIPT = Path(__file__).resolve().parents[1] / "data" / "vet_transcript.jsonl"
_TRANSCRIPT_LOCK = __import__("threading").Lock()


def _log_transcript(entry: dict) -> None:
    """Append every verdict with its reasoning. The final booleans in the
    library never explain THEMSELVES — when a filter looks wrong, this file
    is what you read to find out why it decided that."""
    try:
        TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
        with _TRANSCRIPT_LOCK, open(TRANSCRIPT, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
                timeout: int = 90, title: str = "", desc: str = "",
                place: str = "") -> tuple[bool | None, str | None, str, str]:
    """Return (authentic, art_form, reason, confidence).

    `reason` is the judge's own one-line account of what it saw. It is kept
    and persisted so every verdict is auditable after the fact — a bare
    boolean tells you nothing about WHY a record was dropped, and the whole
    point of an agentic filter is being able to read back its mistakes."""
    prompt = PROMPT.format(
        path=str(image_path), ethnicity=ethnicity, country=country,
        current_af=current_af,
        title=(title or "(none recorded)")[:200],
        desc=(desc or "(none recorded)")[:600],
        place=(place or "(none recorded)")[:120],
    )
    try:
        res = subprocess.run(
            f'claude --print --dangerously-skip-permissions --no-session-persistence '
            f'--tools Read --model {MODEL}',
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, shell=True,
        )
        if res.returncode != 0:
            return None, None, f"(cli exit {res.returncode})", ""
        out = (res.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return None, None, "(cli timeout)", ""

    authentic: bool | None = None
    art_form: str | None = None
    reason = ""
    confidence = ""
    for line in out.splitlines():
        line_u = line.strip().upper()
        if line_u.startswith(("BELONGS:", "AUTHENTIC:")):
            v = line_u.split(":", 1)[1].strip()
            if "YES" in v:
                authentic = True
            elif "NO" in v:
                authentic = False
        elif line_u.startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
        elif line_u.startswith("CONFIDENCE:"):
            confidence = line_u.split(":", 1)[1].strip().split()[0] if ":" in line_u else ""
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
    if not reason:
        # Model ignored the format — keep its prose so the verdict is still
        # auditable rather than silently discarding the explanation.
        reason = " ".join(out.split())[:300]
    return authentic, art_form, reason, confidence


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


def _record_text(rec: dict) -> tuple[str, str, str]:
    """(title, description, location) — the museum's own words, for the judge
    to weigh alongside the picture. Falls back through `raw` because
    BM/Commons/Europeana populate different fields.

    NOTE on `location`: for Europeana this is the *holding institution's*
    country, not the object's origin (a Sarawak Iban pua kumbu reads
    "Sweden" because Gothenburg's Museum of World Culture owns it). It is
    passed through labelled as ambiguous and the prompt is explicit that a
    European holding country is never grounds for rejection. The real origin
    usually sits in dcDescription, so we join several description entries
    rather than only the first."""
    p = rec.get("physical") or {}
    raw = rec.get("raw") or {}
    title = p.get("title") or p.get("classification") or ""
    desc = p.get("physical_description") or p.get("summary") or ""
    if not desc:
        for key in ("dcDescription", "description"):
            v = raw.get(key)
            if isinstance(v, list) and v:
                # Europeana splits origin across entries ("Sarawak",
                # "Malaysia", "Iban") — keep several, not just the first.
                desc = " | ".join(str(x) for x in v[:6])
                break
            if isinstance(v, str) and v:
                desc = v
                break
    place = (rec.get("location") or {}).get("made_in_place") or ""
    if not place:
        v = raw.get("country")
        if isinstance(v, list) and v:
            place = str(v[0])
    holder = (rec.get("source") or {}).get("museum_name") or ""
    if place and holder:
        place = f"{place}  (held by: {holder})"
    return str(title), str(desc), str(place)


def _vet_library_record(meta_path: Path, rec: dict, tmp: Path):
    """Return (result_dict, meta_path, rec_id) — updates rec in-place is done by caller."""
    cul = rec.get("cultural") or {}
    ethnicity = cul.get("ethnicity") or ""
    country = cul.get("country") or ""
    current_af = cul.get("art_form") or "unclassified"
    r_title, r_desc, r_place = _record_text(rec)
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
    authentic, art_form, reason, confidence = _ask_claude(
        img_path, ethnicity, country, current_af,
        title=r_title, desc=r_desc, place=r_place)
    if dl_tmp is not None:
        try: dl_tmp.unlink()
        except Exception: pass
    return ({"authentic": authentic, "art_form": art_form,
             "reason": reason, "confidence": confidence},
            meta_path, rec.get("id"))


def _vet_library(workers: int, force: bool, only: str | None,
                 source: str | None = None, limit: int = 0, seed: int = 0,
                 dry_run: bool = False) -> None:
    """Vet every library object. Persists per-file (batched).

    `--source`/`--limit`/`--seed`/`--dry-run` exist for calibration: they let
    you measure the judge's per-museum reject rate on a random sample and read
    the verdicts before letting it write anything into the library."""
    needle = (only or "").lower()
    # Group records by meta_path so we write once per file.
    candidates: list[tuple[Path, dict]] = []
    for meta_path, r in _iter_library_records():
        cul = r.get("cultural") or {}
        eth = (cul.get("ethnicity") or "").lower()
        if needle and needle not in eth:
            continue
        if source and (r.get("source") or {}).get("museum") != source:
            continue
        # Resume on a real verdict only. A failed call (CLI error, quota
        # exhaustion, download failure) writes vision_vetted=None, and the
        # key's mere PRESENCE used to count as "done" — so after the
        # 2026-08-27 run died on quota, a plain restart skipped all 3538
        # failed records and did nothing. Retry anything without a boolean.
        if not force and cul.get("vision_vetted") is not None:
            continue
        candidates.append((meta_path, r))

    if limit and len(candidates) > limit:
        import random as _random
        _random.Random(seed).shuffle(candidates)
        candidates = candidates[:limit]

    by_file: dict[Path, list[dict]] = {}
    for meta_path, r in candidates:
        by_file.setdefault(meta_path, []).append(r)

    total_targets = sum(len(v) for v in by_file.values())
    print(f"[lib] {len(by_file)} files, {total_targets} records to vet"
          f"{' (DRY RUN — nothing persisted)' if dry_run else ''}")

    # One GLOBAL pool across every record. Vetting used to build a fresh pool
    # per metadata.json, which put a barrier between files: with ~6.5 records
    # per file, 8 workers only ever had 6.5 calls in flight and throughput was
    # pinned at (records-per-file / call-latency) no matter how many workers
    # were requested. Measured 14 rec/min at --workers 8 before this change.
    # Each file is written once its last record lands.
    tally: Counter = Counter()
    loaded: dict[Path, list] = {}
    index: dict[Path, dict] = {}
    remaining: dict[Path, int] = {}
    for meta_path, recs in by_file.items():
        loaded[meta_path] = json.loads(meta_path.read_text(encoding="utf-8"))
        index[meta_path] = {r.get("id"): r for r in loaded[meta_path]}
        remaining[meta_path] = len(recs)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_vet_library_record, mp, r, tmp)
                    for mp, recs in by_file.items() for r in recs]
            for f in as_completed(futs):
                result, meta_path, rec_id = f.result()
                done += 1
                target = index[meta_path].get(rec_id)
                remaining[meta_path] -= 1
                if target is not None:
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
                        cul["vision_reason"] = result.get("reason") or ""
                        cul["vision_confidence"] = result.get("confidence") or ""
                        if af and af in VALID_ART_FORMS and af != "unclassified":
                            cul["art_form_vision"] = af
                        mark = "✓" if auth else ("✗" if auth is False else "?")
                    _src = (target.get("source") or {}).get("museum", "?")
                    tally[(_src, mark)] += 1
                    title = (target.get("physical") or {}).get("title") or rec_id
                    _af_now = cul.get("art_form")
                    _af_new = result.get("art_form")
                    _moved = (f"  {_af_now}→{_af_new}"
                              if _af_new and _af_new != _af_now else "")
                    print(f"  {mark} {_src:<15} [{cul.get('ethnicity','?')}] "
                          f"{str(title)[:46]}{_moved}  ({done}/{total_targets})", flush=True)
                    if result.get("reason"):
                        print(f"      └─ {result['reason'][:150]}", flush=True)
                    _log_transcript({
                        "id": rec_id,
                        "source": _src,
                        "ethnicity": cul.get("ethnicity"),
                        "title": str(title)[:120],
                        "art_form_before": _af_now,
                        "art_form_after": _af_new,
                        "belongs": result.get("authentic"),
                        "confidence": result.get("confidence"),
                        "reason": result.get("reason"),
                    })
                # Last record of this file landed — persist it now.
                if remaining[meta_path] == 0 and not dry_run:
                    meta_path.write_text(
                        json.dumps(loaded[meta_path], indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print("\n=== verdicts by source ===")
    print(f"{'source':<16}{'kept':>7}{'dropped':>9}{'error':>7}{'drop %':>9}")
    for src in sorted({s for s, _ in tally}):
        ok, no, err = tally[(src, "✓")], tally[(src, "✗")], tally[(src, "?")]
        judged = ok + no
        rate = f"{100 * no / judged:.0f}%" if judged else "-"
        print(f"{src:<16}{ok:>7}{no:>9}{err:>7}{rate:>9}")


# -----------------------------------------------------------------------------
# Commons-sidecar vetting (re-uses existing sidecar structure)
# -----------------------------------------------------------------------------

def _vet_commons_photo(url: str, ethnicity: str, country: str, tmp: Path,
                       title: str = "", desc: str = ""):
    ext = ".jpg"
    dst = tmp / f"vet_{abs(hash(url))}{ext}"
    if not _download(url, dst):
        return {"skip": "download-failed"}
    authentic, art_form, reason, confidence = _ask_claude(
        dst, ethnicity, country, "photo", title=title, desc=desc)
    try:
        dst.unlink()
    except Exception:
        pass
    return {"authentic": authentic, "art_form": art_form,
            "reason": reason, "confidence": confidence}


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
                                  ethn, country, tmp,
                                  cp.get("title") or "",
                                  cp.get("description") or ""): cp for cp in targets}
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
                                  ethn, country, tmp,
                                  cp.get("title") or "",
                                  cp.get("description") or ""): cp for cp in targets}
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
    ap.add_argument("--source", help="Only vet records from this museum (met, va, british_museum, ...)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Vet at most N randomly-sampled records (calibration).")
    ap.add_argument("--seed", type=int, default=0, help="Sampling seed for --limit.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print verdicts without writing anything to the library.")
    ap.add_argument("--recheck-rejected", action="store_true",
                    help="Second-pass over vetted=False items with the current (updated) prompt.")
    args = ap.parse_args()

    if args.recheck_rejected:
        _recheck_rejected(args.workers, args.only)
        return

    if args.target in ("commons", "all"):
        _vet_commons(args.workers, args.force, args.only)
    if args.target in ("library", "all"):
        _vet_library(args.workers, args.force, args.only,
                     source=args.source, limit=args.limit, seed=args.seed,
                     dry_run=args.dry_run)


if __name__ == "__main__":
    main()
