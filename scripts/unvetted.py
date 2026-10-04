"""Resolve and publish museum candidates that have not passed the visual vetter.

Commands:

``resolve [--only KEY ...] [--limit N]``
    Resolve eligible rows from ``data/world/candidates.jsonl`` and cache one
    detail response per ``(source, id)`` in ``data/world/unvetted_details.jsonl``.

``build [--only KEY ...]``
    Join the cached details to the candidate rows and ethnicity shards, writing
    the separate ``data/unvetted/`` page shards and index consumed by the site.

The resolver deliberately keeps the filtering conservative: candidate names or
resolved titles containing the human-remains terms below are excluded even when
that can produce a false positive. Non-British-Museum library membership is
checked cheaply from ``data/objects`` filename stems using raw and
source-prefixed ID variants; BM membership uses its canonical ``_in_library``
helper.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Iterable

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from folk_patterns.museums.british_museum import _client, _in_library  # noqa: E402
from world_peoples import UA, _Blocked, _detail  # noqa: E402

WORLD_DIR = REPO / "data" / "world"
DATA_DIR = REPO / "data"
ETHNICITIES_DIR = DATA_DIR / "ethnicities"
OBJECTS_DIR = DATA_DIR / "objects"
UNVETTED_DIR = DATA_DIR / "unvetted"

UNVETTED_STATUSES = {"not_reached", "awaiting_judge", "fetch_failed", "unclassified"}
HUMAN_REMAINS_RE = re.compile(
    r"cranium|kranium|skull|skalle|\btooth\b|\bteeth\b|\btand\b|human remains|mummy|bone|\bben\b",
    re.I,
)
AF_ORDER = [
    "textile", "garment", "architectural", "wallpaper", "ceramic", "jewelry",
    "metalwork", "arms", "masks-ritual", "sculpture", "instruments",
    "painting-mss", "household", "unclassified", "photo",
]


def _jsonl(path: Path) -> list[dict]:
    """Read JSONL, ignoring a malformed/truncated line (especially the tail)."""
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _only(row: dict, wanted: set[str]) -> bool:
    return not wanted or str(row.get("key", "")).casefold() in wanted


def _exclusions() -> set[tuple[str, str, str]]:
    path = WORLD_DIR / "pick_exclusions.json"
    if not path.exists():
        return set()
    try:
        return {
            (str(x.get("key", "")), str(x.get("source", "")), str(x.get("id", "")))
            for x in json.loads(path.read_text(encoding="utf-8"))
            if isinstance(x, dict)
        }
    except (json.JSONDecodeError, TypeError):
        return set()


def _coverage() -> dict[tuple[str, str, str], str]:
    return {
        (str(x.get("key", "")), str(x.get("source", "")), str(x.get("id", ""))): str(x.get("status", ""))
        for x in _jsonl(WORLD_DIR / "pick_coverage.jsonl")
    }


def _object_stems() -> set[str]:
    if not OBJECTS_DIR.exists():
        return set()
    return {p.stem for p in OBJECTS_DIR.glob("*.json")}


def _already_in_library(obj: dict, object_stems: set[str]) -> bool:
    source = str(obj.get("source", ""))
    oid = str(obj.get("id", ""))
    if source == "bm":
        return _in_library(oid)
    clean = oid.lstrip("/")
    # build_index writes canonical IDs, while candidate rows contain museum
    # source IDs. Check both forms; this avoids scanning library metadata per row.
    canonical = re.sub(r"[^A-Za-z0-9]+", "_", clean).strip("_")
    return any(candidate in object_stems for candidate in (
        oid, clean, f"{source}-{oid}", f"{source}-{clean}", f"{source}-{canonical}"
    ))


def _append_detail(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def _detail_error(detail: dict | None) -> str:
    if not detail:
        return "no detail"
    if not detail.get("image_url"):
        return "no image"
    return ""


def cmd_resolve(only: list[str], limit: int = 0) -> None:
    wanted = {x.casefold() for x in only}
    candidates = [r for r in _jsonl(WORLD_DIR / "candidates.jsonl") if _only(r, wanted)]
    coverage = _coverage()
    exclusions = _exclusions()
    cache_path = WORLD_DIR / "unvetted_details.jsonl"
    cached_rows = _jsonl(cache_path)
    cached = {(str(x.get("source", "")), str(x.get("id", ""))): x for x in cached_rows}
    # Rewriting also drops an incomplete tail before new lines are appended.
    if cache_path.exists():
        _write_jsonl(cache_path, cached.values())

    object_stems = _object_stems()
    bm_client = None
    http = httpx.Client(timeout=45, follow_redirects=True, headers=UA)
    processed = 0
    resolved = 0
    bm_refreshed = False

    def record(key: str, obj: dict, detail: dict | None, error: str = "") -> None:
        nonlocal resolved
        title = (detail or {}).get("title") or obj.get("name") or ""
        image_url = (detail or {}).get("image_url") or ""
        remains = bool(HUMAN_REMAINS_RE.search(f"{obj.get('name', '')} {title}"))
        ok = bool(detail and image_url and not remains)
        row = {
            "key": key, "source": obj["source"], "id": obj["id"],
            "title": title, "image_url": image_url, "ok": ok,
            "at": date.today().isoformat(),
        }
        if error or not ok:
            row["error"] = error or ("human remains" if remains else _detail_error(detail))
        _append_detail(cache_path, row)
        cached[(obj["source"], obj["id"])] = row
        resolved += 1

    try:
        for candidate in candidates:
            key = str(candidate.get("key", ""))
            for category, objects in (candidate.get("objects") or {}).items():
                for obj in objects or []:
                    if limit and processed >= limit:
                        print(f"limit reached after {processed} candidates", flush=True)
                        return
                    source, oid = str(obj.get("source", "")), str(obj.get("id", ""))
                    triple = (key, source, oid)
                    if coverage.get(triple) not in UNVETTED_STATUSES:
                        continue
                    if triple in exclusions or HUMAN_REMAINS_RE.search(str(obj.get("name", ""))):
                        continue
                    if _already_in_library(obj, object_stems):
                        continue
                    if (source, oid) in cached:
                        continue

                    processed += 1
                    detail = None
                    error = ""
                    try:
                        if source == "bm" and bm_client is None and os.environ.get("BM_CDP_URL"):
                            bm_client = _client()
                        try:
                            detail = _detail(obj, bm_client, http)
                        except _Blocked:
                            if source != "bm" or bm_refreshed:
                                raise
                            print("British Museum 403 - refreshing cookies", flush=True)
                            bm_client = _client()
                            bm_refreshed = True
                            detail = _detail(obj, bm_client, http)
                    except _Blocked:
                        error = "British Museum still blocked after cookie refresh"
                    except Exception as exc:  # one museum failure must not stop a resumable run
                        error = type(exc).__name__
                    if detail and HUMAN_REMAINS_RE.search(str(detail.get("title") or "")):
                        error = "human remains"
                    record(key, obj, detail, error)
                    if processed % 25 == 0:
                        print(f"resolved {processed} candidates ({resolved} cache lines added)", flush=True)
    finally:
        http.close()
    print(f"resolved {resolved} candidates; cache has {len(cached)} objects", flush=True)


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _name_variants(value: str) -> set[str]:
    n = _norm(value)
    variants = {n}
    if n.endswith("s"):
        variants.add(n[:-1])
    return {x for x in variants if x}


def _candidate_name(candidate: dict, pick_name: str = "") -> str:
    if pick_name:
        return pick_name
    if candidate.get("atlas"):
        return str(candidate["atlas"])
    return re.sub(r"\s+peoples?$", "", str(candidate.get("label", "")), flags=re.I)


def _ethnicity_shards(candidate: dict) -> list[dict]:
    pick_path = WORLD_DIR / "picks" / f"{candidate.get('key')}.json"
    pick_name = ""
    if pick_path.exists():
        try:
            pick_name = str(json.loads(pick_path.read_text(encoding="utf-8")).get("name") or "")
        except json.JSONDecodeError:
            pass
    name_variants = _name_variants(_candidate_name(candidate, pick_name))
    matches = []
    for path in sorted(ETHNICITIES_DIR.glob("*.json")):
        try:
            shard = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if _name_variants(str(shard.get("ethnicity", ""))) & name_variants:
            shard.setdefault("_ethkey", path.stem)
            matches.append(shard)
    if len(matches) > 1:
        country = _norm(str(candidate.get("country", "")))
        same_country = [m for m in matches if _norm(str(m.get("country", ""))) == country]
        if same_country:
            matches = same_country
    return matches


def _object_url(source: str, oid: str) -> str:
    if source == "bm":
        return f"https://www.britishmuseum.org/collection/object/{oid}"
    if source == "europeana":
        return f"https://www.europeana.eu/item{oid}"
    if source == "met":
        return f"https://www.metmuseum.org/art/collection/search/{oid}"
    if source == "cleveland":
        return f"https://clevelandart.org/art/{oid}"
    return ""


def cmd_build(only: list[str]) -> None:
    wanted = {x.casefold() for x in only}
    candidates = [r for r in _jsonl(WORLD_DIR / "candidates.jsonl") if _only(r, wanted)]
    coverage = _coverage()
    exclusions = _exclusions()
    details = {(str(x.get("source", "")), str(x.get("id", ""))): x for x in _jsonl(WORLD_DIR / "unvetted_details.jsonl")}
    old_index: dict[str, int] = {}
    index_path = UNVETTED_DIR / "index.json"
    if only and index_path.exists():
        try:
            old_index = {str(k): int(v) for k, v in json.loads(index_path.read_text(encoding="utf-8")).items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            old_index = {}

    written: dict[str, int] = {}
    unmatched: list[str] = []
    for candidate in candidates:
        matches = _ethnicity_shards(candidate)
        if not matches:
            unmatched.append(str(candidate.get("key", "")))
            continue
        meta = matches[0]
        ethkey = str(meta.get("key") or meta.get("_ethkey") or "")
        buckets: dict[str, list[dict]] = {}
        other: list[dict] = []
        for category, objects in (candidate.get("objects") or {}).items():
            for obj in objects or []:
                source, oid = str(obj.get("source", "")), str(obj.get("id", ""))
                if coverage.get((str(candidate.get("key", "")), source, oid)) not in UNVETTED_STATUSES:
                    continue
                if (str(candidate.get("key", "")), source, oid) in exclusions:
                    continue
                cached = details.get((source, oid))
                if not cached or not cached.get("ok") or not cached.get("image_url"):
                    continue
                if HUMAN_REMAINS_RE.search(f"{obj.get('name', '')} {cached.get('title', '')}"):
                    continue
                item = {
                    "id": oid, "source": source,
                    "title": cached.get("title") or obj.get("name") or "",
                    "image": cached["image_url"], "object_url": _object_url(source, oid),
                }
                if category == "unclassified":
                    other.append(item)
                else:
                    buckets.setdefault(category, []).append(item)
        count = sum(len(items) for items in buckets.values()) + len(other)
        shard = {
            "ethnicity_key": ethkey, "people_key": candidate["key"], "count": count,
            "buckets": buckets, "other": other,
        }
        UNVETTED_DIR.mkdir(parents=True, exist_ok=True)
        (UNVETTED_DIR / f"{ethkey}.json").write_text(json.dumps(shard, indent=2, ensure_ascii=False), encoding="utf-8")
        written[ethkey] = count
        print(f"{candidate.get('label', candidate.get('key'))}: {count}", flush=True)

    if unmatched:
        print("Unmatched keys: " + ", ".join(unmatched), flush=True)

    UNVETTED_DIR.mkdir(parents=True, exist_ok=True)
    if only:
        old_index.update(written)
        for candidate in candidates:
            matches = _ethnicity_shards(candidate)
            if matches:
                match_key = str(matches[0].get("key") or matches[0].get("_ethkey") or "")
                if match_key not in written:
                    old_index.pop(match_key, None)
        final_index = old_index
    else:
        final_index = written
        for path in UNVETTED_DIR.glob("*.json"):
            if path.name != "index.json" and path.stem not in written:
                path.unlink()
    index_path.write_text(json.dumps(final_index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Total: {sum(final_index.values())}", flush=True)


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve", help="cache details for unvetted candidates")
    resolve.add_argument("--only", nargs="*", default=[])
    resolve.add_argument("--limit", type=int, default=0)
    build = sub.add_parser("build", help="write site-facing unvetted shards")
    build.add_argument("--only", nargs="*", default=[])
    args = ap.parse_args()
    if args.command == "resolve":
        cmd_resolve(args.only, args.limit)
    else:
        cmd_build(args.only)


if __name__ == "__main__":
    main()
