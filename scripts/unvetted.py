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
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

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

UNVETTED_STATUSES = {"not_reached", "awaiting_judge", "fetch_failed", "unclassified", "no_pick"}
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


def _has_pick(key: str) -> bool:
    return (WORLD_DIR / "picks" / f"{key}.json").exists()


def _candidate_status(key: str, triple: tuple[str, str, str], coverage: dict[tuple[str, str, str], str]) -> str:
    """A no-pick people has no judge gate: every candidate is text-only."""
    return coverage.get(triple, "") if _has_pick(key) else "no_pick"


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
    met_failures = 0

    def record(key: str, obj: dict, detail: dict | None, error: str = "", status: str = "") -> None:
        nonlocal resolved
        title = (detail or {}).get("title") or obj.get("name") or ""
        image_url = (detail or {}).get("image_url") or ""
        remains = bool(HUMAN_REMAINS_RE.search(f"{obj.get('name', '')} {title}"))
        ok = bool(detail and image_url and not remains)
        row = {
            "key": key, "source": obj["source"], "id": obj["id"],
            "title": title, "image_url": image_url, "ok": ok, "status": status,
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
                    status = _candidate_status(key, triple, coverage)
                    if _has_pick(key) and status not in UNVETTED_STATUSES:
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
                    if source == "met":
                        # Unthrottled, the Met API failed every request after ~2,000
                        # in a row (2026-10-04) and answered again minutes later. An
                        # error status (raised by _detail) is left uncached so a rerun
                        # retries it; an object without an open image is cached as such.
                        time.sleep(0.25)
                        if error:
                            met_failures += 1
                            if met_failures % 10 == 0:
                                # The Met sits behind Imperva, which blocks the session
                                # cookie; a fresh client answered at once (2026-10-05).
                                print(f"Met: {met_failures} errors in a row ({error}), new client after 30 s", flush=True)
                                http.close()
                                time.sleep(30)
                                http = httpx.Client(timeout=45, follow_redirects=True, headers=UA)
                            continue
                        met_failures = 0
                    record(key, obj, detail, error, status)
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


_SHARDS: list[tuple[dict, set[str]]] | None = None


def _vetted_shards() -> list[tuple[dict, set[str]]]:
    """Vetted ethnicity shards with their name variants, read once per run.

    Stub shards (``unvetted_only``) are left out: build_index writes them into
    data/ethnicities/ too, and matching a people against its own stub would turn
    it into a "matched" culture and drop it from stubs.json on the next build."""
    global _SHARDS
    if _SHARDS is None:
        _SHARDS = []
        for path in sorted(ETHNICITIES_DIR.glob("*.json")):
            try:
                shard = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if shard.get("unvetted_only"):
                continue
            shard.setdefault("_ethkey", path.stem)
            _SHARDS.append((shard, _name_variants(str(shard.get("ethnicity", "")))))
    return _SHARDS


def _ethnicity_shards(candidate: dict) -> list[dict]:
    pick_path = WORLD_DIR / "picks" / f"{candidate.get('key')}.json"
    pick_name = ""
    if pick_path.exists():
        try:
            pick_name = str(json.loads(pick_path.read_text(encoding="utf-8")).get("name") or "")
        except json.JSONDecodeError:
            pass
    name_variants = _name_variants(_candidate_name(candidate, pick_name))
    matches = [dict(shard) for shard, variants in _vetted_shards() if variants & name_variants]
    if len(matches) > 1:
        country = _norm(str(candidate.get("country", "")))
        same_country = [m for m in matches if _norm(str(m.get("country", ""))) == country]
        if same_country:
            matches = same_country
    return matches


def _stub_name(candidate: dict) -> str:
    pick_path = WORLD_DIR / "picks" / f"{candidate.get('key')}.json"
    name = ""
    if pick_path.exists():
        try:
            name = str(json.loads(pick_path.read_text(encoding="utf-8")).get("name") or "")
        except (OSError, json.JSONDecodeError):
            pass
    name = name or str(candidate.get("label") or "")
    return re.sub(r"\s+peoples?$", "", name, flags=re.I).strip()


def _stub_key(region: str, country: str, ethnicity: str) -> str:
    from slugify import slugify
    return "__".join(slugify(x) for x in (region, country, ethnicity))


def _geo_points(value) -> Iterable[tuple[float, float]]:
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
        yield float(value[0]), float(value[1])
    elif isinstance(value, list):
        for child in value:
            yield from _geo_points(child)


def _ring_centroid(ring: list) -> tuple[float, float, float]:
    """Return (area, latitude, longitude) for a GeoJSON outer ring."""
    points = [(float(p[0]), float(p[1])) for p in ring if isinstance(p, list) and len(p) >= 2]
    if len(points) < 3:
        if not points:
            return 0.0, 0.0, 0.0
        return 0.0, sum(p[1] for p in points) / len(points), sum(p[0] for p in points) / len(points)
    twice_area = 0.0
    lon_sum = lat_sum = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        cross = x1 * y2 - x2 * y1
        twice_area += cross
        lon_sum += (x1 + x2) * cross
        lat_sum += (y1 + y2) * cross
    if abs(twice_area) < 1e-12:
        return 0.0, sum(p[1] for p in points) / len(points), sum(p[0] for p in points) / len(points)
    return abs(twice_area) / 2, lat_sum / (3 * twice_area), lon_sum / (3 * twice_area)


_GEOJSON: dict[str, dict] = {}
# Wikidata country label -> name in world-countries.geojson.
_COUNTRY_ALIASES = {
    "United States": "United States of America",
    "Tanzania": "United Republic of Tanzania",
    "North Macedonia": "Macedonia",
    "Côte d'Ivoire": "Ivory Coast",
    "Serbia": "Republic of Serbia",
}
# Island states too small for the 1:110m country polygons.
_EUROPEAN_RUSSIA = (59.0, 42.0)
_NORTH_CAUCASUS = (43.2, 45.0)
_COUNTRY_POINTS = {"Samoa": (-13.76, -172.1), "Tonga": (-21.18, -175.2), "Maldives": (3.2, 73.22)}


def _country_centroid(country: str, geojson_path: Path | None = None) -> tuple[float, float] | None:
    path = geojson_path or (REPO / "site" / "public" / "data" / "world-countries.geojson")
    data = _GEOJSON.get(str(path))
    if data is None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        _GEOJSON[str(path)] = data
    plain = country.split("(", 1)[0].strip()
    if plain in _COUNTRY_POINTS:
        return _COUNTRY_POINTS[plain]
    wanted = _norm(_COUNTRY_ALIASES.get(plain, plain))
    for feature in data.get("features", []):
        name = str((feature.get("properties") or {}).get("name") or "")
        if _norm(name) != wanted:
            continue
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates")
        rings = []
        if geometry.get("type") == "Polygon":
            rings = [coordinates[0]] if coordinates else []
        elif geometry.get("type") == "MultiPolygon":
            rings = [polygon[0] for polygon in (coordinates or []) if polygon]
        centroids = [_ring_centroid(ring) for ring in rings]
        weighted = [(area, lat, lon) for area, lat, lon in centroids if area]
        if weighted:
            total = sum(area for area, _, _ in weighted)
            return (sum(area * lat for area, lat, _ in weighted) / total,
                    sum(area * lon for area, _, lon in weighted) / total)
        points = list(_geo_points(coordinates))
        if not points:
            return None
        return (sum(lat for _, lat in points) / len(points), sum(lon for lon, _ in points) / len(points))
    return None


def _stub_jitter(people_key: str) -> tuple[float, float]:
    digest = hashlib.sha256(people_key.encode("utf-8")).digest()
    return ((digest[0] / 255) * 3 - 1.5, (digest[1] / 255) * 3 - 1.5)


def _site_region(continent: str, region: str) -> str:
    text = f"{continent} {region}".casefold()
    if any(x in text for x in ("middle east", "north africa", "mena")):
        return "middle-east-north-africa"
    if "europe" in text:
        return "europe"
    if "central asia" in text:
        return "central-asia"
    if "south asia" in text:
        return "south-asia"
    if "south east asia" in text or "southeast asia" in text:
        return "southeast-asia"
    if "east asia" in text:
        return "east-asia"
    if "north america" in text or "arctic" in text:
        return "north-america"
    if "america" in text or "andes" in text or "caribbean" in text:
        return "latin-america"
    if "oceania" in text or "melanesia" in text or "micronesia" in text or "polynesia" in text:
        return "oceania"
    if "africa" in text:
        return "sub-saharan-africa"
    continent = continent.casefold()
    return {
        "europe": "europe",
        "asia": "east-asia",
        "americas": "latin-america",
        "north america": "north-america",
        "oceania": "oceania",
        "africa": "sub-saharan-africa",
    }.get(continent, "sub-saharan-africa")


def _existing_country_regions() -> dict[str, str]:
    counts: dict[str, dict[str, int]] = {}
    for path in sorted(ETHNICITIES_DIR.glob("*.json")):
        try:
            shard = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        country = _norm(str(shard.get("country") or ""))
        region = str(shard.get("region") or "")
        if country and region:
            counts.setdefault(country, {})[region] = counts.setdefault(country, {}).get(region, 0) + 1
    return {country: sorted(regions.items(), key=lambda x: (-x[1], x[0]))[0][0]
            for country, regions in counts.items()}


def _stub_for_candidate(candidate: dict, country_regions: dict[str, str]) -> dict | None:
    country = str(candidate.get("country") or "").strip()
    centroid = _country_centroid(country)
    if centroid is None:
        print(f"  country polygon miss: {candidate.get('key')} {country}", flush=True)
        return None
    region = country_regions.get(_norm(country)) or _site_region(str(candidate.get("continent") or ""), str(candidate.get("region") or ""))
    # Russia's atlas cultures are Siberian, so its majority region and its
    # centroid put Komi, Udmurts and Vepsians in Siberia and the Avars there too.
    if country == "Russia" and "caucasus" in str(candidate.get("region") or "").casefold():
        centroid = _NORTH_CAUCASUS
    elif country == "Russia" and str(candidate.get("continent") or "") == "Europe":
        region, centroid = "europe", _EUROPEAN_RUSSIA
    name = _stub_name(candidate)
    lat, lon = centroid
    dlat, dlon = _stub_jitter(str(candidate.get("key") or name))
    return {
        "ethnicity_key": _stub_key(region, country, name),
        "people_key": str(candidate.get("key") or ""),
        "region": region,
        "country": country,
        "ethnicity": name,
        "lat": max(-90, min(90, round(lat + dlat, 5))),
        "lon": round(lon + dlon, 5),
    }


def _tile_image(source: str, image_url: str) -> str:
    """Europeana objects show through Europeana's own thumbnail service: some
    providers' originals need a login (every Finnish Heritage Agency image
    answered 401 on 2026-10-05) while the thumbnail of the same URL loads."""
    if source != "europeana" or image_url.startswith("https://api.europeana.eu/thumbnail/"):
        return image_url
    return "https://api.europeana.eu/thumbnail/v2/url.json?" + urlencode({"uri": image_url, "type": "IMAGE", "size": "w400"})


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
    global _SHARDS
    _SHARDS = None
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
    country_regions = _existing_country_regions()
    stubs_path = UNVETTED_DIR / "stubs.json"
    try:
        old_stubs = json.loads(stubs_path.read_text(encoding="utf-8")) if stubs_path.exists() else []
    except (OSError, json.JSONDecodeError):
        old_stubs = []
    stubs = {str(x.get("people_key")): x for x in old_stubs if isinstance(x, dict) and x.get("people_key")}
    targeted_keys = {str(candidate.get("key", "")) for candidate in candidates}
    if only:
        for key in targeted_keys:
            old_stub = stubs.pop(key, None)
            if old_stub:
                old_index.pop(str(old_stub.get("ethnicity_key") or ""), None)
    else:
        stubs = {}
    for candidate in candidates:
        matches = _ethnicity_shards(candidate)
        stub = False
        if matches:
            meta = matches[0]
            ethkey = str(meta.get("key") or meta.get("_ethkey") or "")
        else:
            meta = _stub_for_candidate(candidate, country_regions)
            if not meta:
                unmatched.append(str(candidate.get("key", "")))
                continue
            ethkey = meta["ethnicity_key"]
            stubs[str(candidate.get("key", ""))] = meta
            stub = True
        buckets: dict[str, list[dict]] = {}
        other: list[dict] = []
        for category, objects in (candidate.get("objects") or {}).items():
            for obj in objects or []:
                source, oid = str(obj.get("source", "")), str(obj.get("id", ""))
                triple = (str(candidate.get("key", "")), source, oid)
                status = _candidate_status(str(candidate.get("key", "")), triple, coverage)
                if _has_pick(str(candidate.get("key", ""))) and status not in UNVETTED_STATUSES:
                    continue
                if triple in exclusions:
                    continue
                cached = details.get((source, oid))
                if not cached or not cached.get("ok") or not cached.get("image_url"):
                    continue
                if HUMAN_REMAINS_RE.search(f"{obj.get('name', '')} {cached.get('title', '')}"):
                    continue
                item = {
                    "id": oid, "source": source,
                    "title": cached.get("title") or obj.get("name") or "",
                    "image": _tile_image(source, cached["image_url"]), "object_url": _object_url(source, oid),
                }
                if category == "unclassified":
                    other.append(item)
                else:
                    buckets.setdefault(category, []).append(item)
        count = sum(len(items) for items in buckets.values()) + len(other)
        if stub and not count:
            # A culture with nothing to show is not put on the map.
            stubs.pop(str(candidate.get("key", "")), None)
            continue
        shard = {
            "ethnicity_key": ethkey, "people_key": candidate["key"], "count": count,
            "buckets": buckets, "other": other,
        }
        UNVETTED_DIR.mkdir(parents=True, exist_ok=True)
        (UNVETTED_DIR / f"{ethkey}.json").write_text(json.dumps(shard, indent=2, ensure_ascii=False), encoding="utf-8")
        written[ethkey] = count
        print(f"{candidate.get('label', candidate.get('key'))}: {count}{' (stub)' if stub else ''}", flush=True)

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
    stubs_path.write_text(json.dumps(sorted(stubs.values(), key=lambda x: x["ethnicity_key"]), indent=2, ensure_ascii=False), encoding="utf-8")
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
