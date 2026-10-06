"""Wikimedia Commons photos for stub cultures with few or no museum objects.

Stub cultures (text-only world-list peoples) never went through the Commons
pipeline the vetted cultures did. This fills the ones with fewer than 20
unreviewed museum objects in two steps:

    python scripts/stub_commons.py gather              # free: Wikidata + Commons API
    python scripts/stub_commons.py review --limit 10   # Codex, one contact sheet per culture
    python scripts/stub_commons.py review              # the rest
    python scripts/stub_commons.py dedupe              # drop near-duplicate kept photos

`gather` takes the people's Commons category from Wikidata (P373), the
subcategories whose names point at costume, craft, art or culture (a broad
"X people" category is mostly maps, documents and famous individuals), and
the images embedded in its Wikipedia article. Up to 15 candidates per culture
go to `work/stub-commons/candidates.jsonl`.

`review` puts one culture's candidates on one numbered contact sheet and asks
Codex once, with the same criteria as `commons_editorial.py`. That single
pass is the only check: the photos are published as unreviewed material,
like the stub's museum objects, and a person only samples the sheets. Every
verdict, kept or not, is written to the culture's media sidecar under
content/media/, and the raw replies to work/stub-commons/transcript.jsonl.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from slugify import slugify  # noqa: E402

from commons_editorial import (REVIEW_SCHEMA, _contact_sheet,  # noqa: E402
                               _load_cached_or_download, _parse_reviews, build_prompt)
from folk_patterns.codex_cli import ask  # noqa: E402
from folk_patterns.media import _get, commons_fetch_photos, commons_from_wiki_article  # noqa: E402

WORK = ROOT / "work" / "stub-commons"
CANDIDATES = WORK / "candidates.jsonl"
TRANSCRIPT = WORK / "transcript.jsonl"
MEDIA_DIR = ROOT / "content" / "media"
MAX_CANDIDATES = 15

KEEP_SUBCAT = re.compile(
    r"cloth|costume|dress|embroider|craft|textile|carpet|rug|jewel|\bart\b|in art|cuisine|"
    r"culture|dance|music|instrument|pottery|ceramic|weav|architect|house|village|"
    r"tradition|folk|chokha|ornament|pattern|festival|ritual|mask", re.I)
# Added to commons_editorial's criteria: the stub galleries are a pattern and
# craft atlas, so ritual butchery and everyday snapshots do not belong there.
EXTRA_CRITERIA = """Also FAIL: slaughter, carcasses, blood or other graphic scenes, even
when they are part of a ritual; and snapshots of people in ordinary modern
clothing with no visible traditional dress, craft or performance."""
# A stub with a handful of objects is as thin as one with none.
PHOTO_BELOW = 20
DROP_SUBCAT = re.compile(r"language|map|by country|people from|politic|sport|footbal|wrestl", re.I)


def _targets() -> list[dict]:
    """Stubs with fewer than PHOTO_BELOW unreviewed museum objects."""
    stubs = json.loads((ROOT / "data" / "unvetted" / "stubs.json").read_text(encoding="utf-8"))
    out = []
    for s in stubs:
        p = ROOT / "data" / "unvetted" / f"{s['ethnicity_key']}.json"
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        if (len(d) if isinstance(d, list) else d.get("count") or 0) < PHOTO_BELOW:
            out.append(s)
    return out


def _sidecar(stub: dict) -> Path:
    return MEDIA_DIR / stub["region"] / f"{slugify(stub['country'])}__{slugify(stub['ethnicity'])}.json"


def _p373(qids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(qids), 50):
        r = _get("https://www.wikidata.org/w/api.php", params={
            "action": "wbgetentities", "ids": "|".join(qids[i:i + 50]),
            "props": "claims", "format": "json"})
        for qid, ent in (r.json().get("entities") or {}).items():
            claim = ((ent.get("claims") or {}).get("P373") or [{}])[0]
            val = (claim.get("mainsnak") or {}).get("datavalue", {}).get("value")
            if val:
                out[qid] = val
    return out


def _subcats(category: str) -> list[str]:
    r = _get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "list": "categorymembers", "cmtitle": f"Category:{category}",
        "cmtype": "subcat", "cmlimit": 200, "format": "json"})
    names = [m["title"].split(":", 1)[1] for m in r.json().get("query", {}).get("categorymembers", [])]
    return [n for n in names if KEEP_SUBCAT.search(n) and not DROP_SUBCAT.search(n)]


def _gather_one(stub: dict, category: str | None, wiki_title: str | None) -> dict:
    recs: list[dict] = []
    seen: set[str] = set()

    def add(rows: list[dict], source: str) -> None:
        for r in rows:
            if r["title"] not in seen:
                seen.add(r["title"])
                recs.append({**r, "source_category": source})

    subs: list[str] = []
    if category:
        subs = _subcats(category)
        for sub in subs[:8]:
            add(commons_fetch_photos(sub, limit=4), sub)
        add(commons_fetch_photos(category, limit=8), category)
    if wiki_title:
        add(commons_from_wiki_article(wiki_title, limit=6), f"Wikipedia article: {wiki_title}")
    return {"key": stub["ethnicity_key"], "category": category, "subcats": subs,
            "photos": recs[:MAX_CANDIDATES]}


def cmd_gather(limit: int, workers: int) -> None:
    done = set()
    if CANDIDATES.exists():
        done = {json.loads(line)["key"] for line in CANDIDATES.open(encoding="utf-8")}
    todo = [s for s in _targets() if s["ethnicity_key"] not in done and not _sidecar(s).exists()]
    if limit:
        todo = todo[:limit]
    titles = json.loads((ROOT / "data" / "world" / "stub_wiki_titles.json").read_text(encoding="utf-8"))
    cats = _p373([s["people_key"] for s in todo if s.get("people_key")])
    print(f"[gather] {len(todo)} stubs, {len(cats)} with a Commons category")
    WORK.mkdir(parents=True, exist_ok=True)

    def job(s: dict) -> dict:
        try:
            return _gather_one(s, cats.get(s.get("people_key")), titles.get(s.get("people_key")))
        except Exception as exc:  # one failed culture does not stop the run
            return {"key": s["ethnicity_key"], "error": str(exc), "photos": []}

    with ThreadPoolExecutor(workers) as pool, CANDIDATES.open("a", encoding="utf-8") as fh:
        for n, row in enumerate(pool.map(job, todo), 1):
            if "error" in row:
                print(f"  [{n}] {row['key']}: error {row['error'][:120]}")
                continue
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"  [{n}] {row['key']}: {len(row['photos'])} photos  cat={row['category']!r} subcats={len(row['subcats'])}")


def _review_one(stub: dict, row: dict) -> str:
    photos = row["photos"]
    tiles, captions = [], []
    for idx, photo in enumerate(photos):
        url = re.sub(r"/\d+px-", "/330px-", photo.get("thumb_url") or "")
        img = _load_cached_or_download(url, WORK / "cache" / row["key"] / f"{idx:02}.jpg")
        if img is not None:
            tiles.append((idx, img))
            captions.append((idx, photo))
    reviews: dict[int, tuple[bool, str]] = {}
    raw = ""
    if tiles:
        sheet = WORK / "sheets" / f"{row['key']}.jpg"
        _contact_sheet(tiles, sheet)
        try:
            raw = ask(build_prompt(stub["ethnicity"], stub["country"], captions)
                      + "\n\n" + EXTRA_CRITERIA,
                      image=sheet.read_bytes(), schema=REVIEW_SCHEMA)
            raw = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            reviews = _parse_reviews(raw, {i for i, _ in tiles})
        except Exception as exc:
            raw = f"ERROR {exc}"
        with TRANSCRIPT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": row["key"], "indices": [i for i, _ in tiles],
                                 "raw_reply": raw}, ensure_ascii=False) + "\n")
        if raw.startswith("ERROR"):
            return f"{row['key']}: {raw[:120]}"
    reviewer = f"codex-stub-sheet-{date.today().isoformat()}"
    for idx, (passed, reason) in reviews.items():
        photos[idx].update(vetted=passed, editorial_reviewed=passed,
                           editorial_reviewer=reviewer, editorial_reason=reason)
    sidecar = _sidecar(stub)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({
        "country": stub["country"], "ethnicity": stub["ethnicity"], "stub": True,
        "commons_category": row.get("category"),
        "sources": {"commons": photos},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    kept = sum(1 for p, _ in reviews.values() if p)
    return f"{row['key']}: kept {kept}/{len(photos)} (unreviewed {len(tiles) - len(reviews)})"


def cmd_review(limit: int, workers: int, only: list[str]) -> None:
    stubs = {s["ethnicity_key"]: s for s in _targets()}
    rows = [json.loads(line) for line in CANDIDATES.open(encoding="utf-8")]
    todo = [r for r in rows if r["key"] in stubs and r["photos"] and not _sidecar(stubs[r["key"]]).exists()
            and (not only or any(o.lower() in r["key"] for o in only))]
    if limit:
        todo = todo[:limit]
    print(f"[review] {len(todo)} cultures, one Codex call each")
    with ThreadPoolExecutor(workers) as pool:
        for n, msg in enumerate(pool.map(lambda r: _review_one(stubs[r["key"]], r), todo), 1):
            print(f"  [{n}/{len(todo)}] {msg}")


def _ahash(path: Path) -> int | None:
    from PIL import Image
    try:
        with Image.open(path) as im:
            px = list(im.convert("L").resize((8, 8)).getdata())
    except (OSError, ValueError):
        return None
    avg = sum(px) / 64
    return sum(1 << i for i, v in enumerate(px) if v > avg)


def cmd_dedupe() -> None:
    """Commons often holds one picture under two file names (a scan and its
    crop, a re-upload). Of kept photos whose 8x8 average hashes differ in at
    most 6 bits, keep the first. Local only, no LLM."""
    dropped = 0
    for p in sorted(MEDIA_DIR.rglob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if not d.get("stub"):
            continue
        key = f"{p.parent.name}__{p.stem}"
        hashes: list[int] = []
        changed = False
        for idx, photo in enumerate(d["sources"]["commons"]):
            if not photo.get("editorial_reviewed"):
                continue
            h = _ahash(WORK / "cache" / key / f"{idx:02}.jpg")
            if h is None:
                continue
            if any(bin(h ^ o).count("1") <= 6 for o in hashes):
                photo.update(vetted=False, editorial_reviewed=False,
                             editorial_reason="Near-duplicate of an earlier kept photo.")
                changed = True
                dropped += 1
            else:
                hashes.append(h)
        if changed:
            p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[dedupe] dropped {dropped} near-duplicates")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["gather", "review", "dedupe"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--only", nargs="+", default=[], help="review only keys containing these")
    a = ap.parse_args()
    if a.step == "gather":
        cmd_gather(a.limit, a.workers or 4)
    elif a.step == "dedupe":
        cmd_dedupe()
    else:
        cmd_review(a.limit, a.workers or 2, a.only)


if __name__ == "__main__":
    main()
