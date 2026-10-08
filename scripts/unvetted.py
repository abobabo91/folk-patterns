"""Resolve and publish museum candidates that have not passed the visual vetter.

Commands:

``resolve [--only KEY ...] [--limit N]``
    Resolve eligible rows from ``data/world/candidates.jsonl`` and cache one
    detail response per ``(source, id)`` in ``data/world/unvetted_details/part-N.jsonl``.

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
import zlib
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
    r"cranium|kranium|skull|skalle|\btooth\b|\bteeth\b|\btand\b|human remains|mummy|bone|\bben\b"
    # French, German and Russian titles from quai Branly, Berlin and the KAMIS
    # museums (quai Branly classes some records "Restes humains")
    r"|crâne|restes humains|ossements|\bos humain|momie|tête réduite|tsantsa|shrunken head"
    r"|schädel|skelett|mumie|menschenknochen|schrumpfkopf|череп|скелет|мумия|останки",
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
    """A no-pick people has no judge gate: every candidate is text-only. A
    candidate harvested after its people's judge run has no coverage row; the
    judge never saw it, so it is "not_reached" (its drops keep their status)."""
    return coverage.get(triple, "not_reached") if _has_pick(key) else "no_pick"


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


# The detail cache is split into DETAIL_SHARDS files by a stable hash of
# (source, id): as one file it reached 82 MB (2026-10-07), past GitHub's 50 MB
# warning, and GitHub refuses files over 100 MB. A record always lands in the
# same shard, so an append touches one file. The single file it replaced,
# unvetted_details.jsonl, is still read and is folded in by the next resolve.
DETAIL_SHARDS = 8


def _details_dir() -> Path:
    return WORLD_DIR / "unvetted_details"


def _legacy_details() -> Path:
    return WORLD_DIR / "unvetted_details.jsonl"


def _detail_shard(source: str, oid: str) -> Path:
    n = zlib.crc32(f"{source}\t{oid}".encode("utf-8")) % DETAIL_SHARDS
    return _details_dir() / f"part-{n}.jsonl"


def _read_details() -> list[dict]:
    """Every cached detail row, the legacy single file first so a shard's row wins."""
    rows = _jsonl(_legacy_details())
    for n in range(DETAIL_SHARDS):
        rows += _jsonl(_details_dir() / f"part-{n}.jsonl")
    return rows


def _write_details(rows: Iterable[dict]) -> None:
    """Rewrite the shards from `rows` (one per (source, id)) and drop the legacy file."""
    parts: dict[Path, list[dict]] = {_details_dir() / f"part-{n}.jsonl": [] for n in range(DETAIL_SHARDS)}
    for r in rows:
        parts[_detail_shard(str(r.get("source", "")), str(r.get("id", "")))].append(r)
    for path, part in parts.items():
        _write_jsonl(path, part)
    if _legacy_details().exists():
        _legacy_details().unlink()


def _append_detail(record: dict) -> None:
    path = _detail_shard(str(record.get("source", "")), str(record.get("id", "")))
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
    cached = {(str(x.get("source", "")), str(x.get("id", ""))): x for x in _read_details()}
    # Rewriting also drops an incomplete tail before new lines are appended.
    if cached:
        _write_details(cached.values())

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
        _append_detail(row)
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

                    if source == "bm" and bm_client is None and not os.environ.get("BM_CDP_URL"):
                        # Without a Chrome's cookie there is no BM client: the object
                        # stays uncached for a run with BM_CDP_URL. Runs without it
                        # cached 1,067 BM objects as "AttributeError" (found 2026-10-08).
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
_PLACES: dict[str, dict] = {}


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


_REGION_CONTINENTS = {
    "sub-saharan-africa": {"Africa"}, "middle-east-north-africa": {"Africa", "Asia"},
    "latin-america": {"Americas", "South America", "North America"},
    "north-america": {"Americas", "North America"}, "europe": {"Europe", "Asia"},
    "east-asia": {"Asia", "Europe"}, "southeast-asia": {"Asia", "Oceania"}, "south-asia": {"Asia"},
    "central-asia": {"Asia", "Europe"}, "caucasus": {"Asia", "Europe"}, "oceania": {"Oceania", "Asia"},
}


def _ethnicity_shards(candidate: dict) -> list[dict]:
    pick_path = WORLD_DIR / "picks" / f"{candidate.get('key')}.json"
    pick_name = ""
    if pick_path.exists():
        try:
            pick_name = str(json.loads(pick_path.read_text(encoding="utf-8")).get("name") or "")
        except json.JSONDecodeError:
            pass
    if candidate.get("key") in _SAME_AS:
        candidate = {**candidate, "atlas": _SAME_AS[candidate["key"]]}
        pick_name = ""
    name_variants = _name_variants(_candidate_name(candidate, pick_name))
    matches = [dict(shard) for shard, variants in _vetted_shards() if variants & name_variants]
    if not candidate.get("atlas") and candidate.get("continent"):
        # A bare name match must stay on the people's continent: the Barí of
        # Colombia matched the Bari of South Sudan and filled its page with 408
        # Colombian objects (2026-10-07). Measured over all candidates, this
        # drops that one match and no other.
        matches = [m for m in matches if candidate["continent"] in
                   _REGION_CONTINENTS.get(str(m.get("region") or (m.get("key") or m.get("_ethkey") or "").split("__")[0]), {candidate["continent"]})]
    if len(matches) > 1:
        country = _norm(str(candidate.get("country", "")))
        same_country = [m for m in matches if _norm(str(m.get("country", ""))) == country]
        if same_country:
            matches = same_country
    return matches


# Stub names fixed by hand after the audit of 2026-10-08 (scripts/audit_places.py):
# the pick or Wikidata label was a misspelling ("Embera peoplee"), a language
# ("Kannada"), a demonym or a tribal-government name ("Fort Yuma Quechan Indian Tribe").
_STUB_NAME_FIX = {
    "Q2603574": "Khinalug", "Q584462": "Batsbi", "Q1479503": "Kists", "Q846578": "Svans",
    "Q244028": "Kabardians", "Q3595760": "Pashai", "Q217815": "Naxi", "Q167395": "Jász",
    "Q47246": "Erzya", "Q836660": "Votians", "Q1028240": "Kayapó",
    "Q34188": "Yanomami", "Q2162816": "Xinka", "Q2025212": "Tepehuán", "Q898658": "Totonac",
    "Q1335017": "Emberá", "Q180688": "Bedouin", "Q7395442": "Sa'idis", "Q2062219": "Opelousa",
    "Q1754503": "Quechan", "Q852431": "Pueblo peoples", "Q3521909": "Bunt", "Q118281": "Kannadigas",
    "Q1241443": "Kondh", "Q156274": "Kota", "Q3428765": "Meo", "Q6932164": "Mughal",
    "Q140713": "Tuluvas", "Q4829787": "Awan", "Q4203419": "Iron Ossetians", "Q340520": "Acehnese",
    "Q2608045": "Cirebonese", "Q633375": "Karo Batak", "Q588870": "Orang Laut",
    "Q3267945": "Americo-Liberians", "Q1726724": "Manjak", "Q58843": "Tuareg", "Q2929727": "Bedik",
    "Q1061544": "Il Chamus", "Q4446080": "Finns proper", "Q4940333": "Bawm", "Q4120474": "Hadhrami",
    "Q1541828": "Riffians", "Q1267932": "Shilha",
}
# Stubs that are the same people as a seeded culture, found by the same audit:
# their objects join the seed's page instead of a second marker.
_SAME_AS = {
    "Q117244": "Arawak", "Q331789": "Crow", "Q1783171": "Navajo", "Q947650": "Osage",
    "Q750947": "Pawnee", "Q6078806": "Lao Isan", "Q216151": "Kinh", "Q1721908": "Kalabari",
}


def _stub_name(candidate: dict) -> str:
    if candidate.get("key") in _STUB_NAME_FIX:
        return _STUB_NAME_FIX[candidate["key"]]
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
    "Timor-Leste": "East Timor",
}
# Island states too small for the 1:110m country polygons.
_EUROPEAN_RUSSIA = (59.0, 42.0)
_NORTH_CAUCASUS = (43.2, 45.0)
_COUNTRY_POINTS = {"Samoa": (-13.76, -172.1), "Tonga": (-21.18, -175.2), "Maldives": (3.2, 73.22),
                   "Isle of Man": (54.23, -4.55), "Federated States of Micronesia": (7.42, 151.85),
                   "Northern Mariana Islands": (15.2, 145.75), "Bahrain": (26.07, 50.55),
                   "Cook Islands": (-21.23, -159.78), "Dominica": (15.42, -61.35), "Bahamas": (24.25, -76.0)}


def _country_rings(country: str) -> list[list]:
    """Outer rings ([lon, lat] pairs) of the country's polygons; [] when unknown."""
    if _country_centroid(country) is None:
        return []
    plain = country.split("(", 1)[0].strip()
    wanted = _norm(_COUNTRY_ALIASES.get(plain, plain))
    for data in _GEOJSON.values():
        for feature in data.get("features", []):
            if _norm(str((feature.get("properties") or {}).get("name") or "")) != wanted:
                continue
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates") or []
            if geometry.get("type") == "Polygon":
                return [coordinates[0]] if coordinates else []
            if geometry.get("type") == "MultiPolygon":
                return [polygon[0] for polygon in coordinates if polygon]
    return []


def _near_country(country: str, point: tuple[float, float], km: float = 500) -> bool | None:
    """Inside the country's polygon or within `km` of its border; None when the
    country has no polygon. A distance to the centroid cannot stand in: Russia's
    centroid is in Siberia, 3,000+ km from the Kalmyks and the Vepsians."""
    rings = _country_rings(country)
    if not rings:
        return None
    lat, lon = point
    for ring in rings:
        inside = False
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        if inside:
            return True
    return any(_km(point, (y, x)) <= km for ring in rings for x, y in ring)


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
    if "caucasus" in text:
        return "caucasus"
    if any(x in text for x in ("middle east", "north africa", "mena", "west asia", "southwest asia", "anatolia",
                               "levant", "mesopotamia", "arabia", "iran")):
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


# Countries whose region the classifier's free text gets wrong: "Asia" alone
# fell through to East Asia (Georgia, Iraq, Lebanon), and existing shards then
# carried the error forward through `country_regions`.
_COUNTRY_REGION = {
    **dict.fromkeys(("Georgia", "Armenia", "Azerbaijan"), "caucasus"),
    **dict.fromkeys(("Iraq", "Iran", "Turkey", "Syria", "Lebanon", "Israel", "Palestine", "Jordan", "Saudi Arabia",
                     "Yemen", "Oman", "United Arab Emirates", "Kuwait", "Qatar", "Bahrain"), "middle-east-north-africa"),
    "Cyprus": "europe",
}
# Per-people exceptions to the country rule. Denmark holds both the Danes and
# the Kalaallit of Greenland, and its majority region comes from the shards of
# the last build, so it flips between builds unless both are pinned.
_STUB_REGION = {"Q164714": "europe", "Q888553": "north-america"}


def _screened_keep() -> set[str]:
    """Peoples `world_peoples.py screen` put on the map (a people, not a
    duplicate; living, or extinct and marked so); data/world/screened.json."""
    p = WORLD_DIR / "screened.json"
    if not p.exists():
        return set()
    return {k for k, v in json.loads(p.read_text(encoding="utf-8")).items() if v.get("verdict") in ("keep", "extinct")}


def _bare_candidate(key: str) -> dict:
    """A candidate row for a people with no museum evidence, from the
    classifier and Wikidata."""
    global _CLASSIFIED, _WIKIDATA
    if _CLASSIFIED is None:
        _CLASSIFIED = json.loads((WORLD_DIR / "classified.json").read_text(encoding="utf-8"))
        _WIKIDATA = {r["qid"]: r for r in json.loads((WORLD_DIR / "wikidata.json").read_text(encoding="utf-8"))}
    c, w = _CLASSIFIED.get(key) or {}, _WIKIDATA.get(key) or {}
    return {"key": key, "label": w.get("label") or key, "continent": c.get("continent"), "region": c.get("region"),
            "country": c.get("country") or w.get("country"), "unvetted_only": True, "objects": {}}


_CLASSIFIED: dict | None = None
_WIKIDATA: dict | None = None


def _stub_for_candidate(candidate: dict, country_regions: dict[str, str]) -> dict | None:
    country = str(candidate.get("country") or "").strip()
    centroid = _country_centroid(country)
    if centroid is None:
        print(f"  country polygon miss: {candidate.get('key')} {country}", flush=True)
        return None
    region = _STUB_REGION.get(str(candidate.get("key") or "")) or _COUNTRY_REGION.get(country) or country_regions.get(_norm(country)) or _site_region(str(candidate.get("continent") or ""), str(candidate.get("region") or ""))
    # Russia's atlas cultures are Siberian, so its majority region and its
    # centroid put Komi, Udmurts and Vepsians in Siberia and the Avars there too.
    place = _PLACES.get(str(candidate.get("key") or ""))
    if place and country not in _OVERSEAS:
        km = _OFFSHORE_KM.get(country, 500)
        near = _near_country(country, (place["lat"], place["lon"]), km)
        if near is False and _near_country(country, (-place["lat"], place["lon"]), km):
            # Codex drops the minus sign now and then: Ovimbundu came back as 12.8 N.
            place = {**place, "lat": -place["lat"]}
        elif near is False or (near is None and _km((place["lat"], place["lon"]), centroid) > 3000):
            place = None   # implausibly far from the country
    if place:
        # A homeland point replaces the centroid; the Russian overrides below
        # only stand in for a missing one.
        centroid = (place["lat"], place["lon"])
        if country == "Russia":
            region = _site_region(str(candidate.get("continent") or ""), str(candidate.get("region") or ""))
    elif country == "Russia" and "caucasus" in str(candidate.get("region") or "").casefold():
        region, centroid = "caucasus", _NORTH_CAUCASUS
    elif country == "Russia" and str(candidate.get("continent") or "") == "Europe":
        region, centroid = "europe", _EUROPEAN_RUSSIA
    name = _stub_name(candidate)
    lat, lon = centroid
    dlat, dlon = _stub_jitter(str(candidate.get("key") or name))
    if place:   # small jitter only; build_index spreads markers that still overlap
        dlat, dlon = dlat / 6, dlon / 6
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
    if source in ("rem", "kunstkamera"):
        from folk_patterns.museums import kamis
        return kamis.object_url(source, oid)
    if source == "smb":
        from folk_patterns.museums import smb
        return smb.object_url(oid)
    if source == "prm":
        from folk_patterns.museums import prm
        return prm.object_url(oid)
    if source == "maa":
        from folk_patterns.museums import maa
        return maa.object_url(oid)
    if source == "quaibranly":
        from folk_patterns.museums import quaibranly
        return quaibranly.object_url(oid)
    if source == "peabody":
        from folk_patterns.museums import peabody
        return peabody.object_url(oid)
    if source == "museudoindio":
        from folk_patterns.museums import museudoindio
        return museudoindio.object_url(oid)
    if source == "ntm":
        from folk_patterns.museums import ntm
        return ntm.object_url(oid)
    if source == "neprajz":
        from folk_patterns.museums import neprajz
        return neprajz.object_url(oid)
    if source == "joconde":
        from folk_patterns.museums import joconde
        return joconde.object_url(oid)
    return ""


def _merge_shards(a: dict, b: dict) -> dict:
    """One shard for two peoples on the same map point, objects deduplicated;
    people_key is the people with more objects, people_keys lists both."""
    big, small = (a, b) if a["count"] >= b["count"] else (b, a)
    seen: set[tuple[str, str]] = set()

    def take(items: list[dict]) -> list[dict]:
        out = []
        for it in items:
            ref = (it["source"], it["id"])
            if ref not in seen:
                seen.add(ref)
                out.append(it)
        return out

    buckets = {cat: take(big["buckets"].get(cat, []) + small["buckets"].get(cat, []))
               for cat in dict.fromkeys([*big["buckets"], *small["buckets"]])}
    other = take(big.get("other", []) + small.get("other", []))
    keys = list(dict.fromkeys((big.get("people_keys") or [big["people_key"]]) + (small.get("people_keys") or [small["people_key"]])))
    return {"ethnicity_key": big["ethnicity_key"], "people_key": big["people_key"], "people_keys": keys,
            "count": sum(len(v) for v in buckets.values()) + len(other), "buckets": buckets, "other": other}


def cmd_build(only: list[str]) -> None:
    global _SHARDS, _PLACES
    _SHARDS = None
    _PLACES = _places()
    wanted = {x.casefold() for x in only}
    candidates = [r for r in _jsonl(WORLD_DIR / "candidates.jsonl") if _only(r, wanted)]
    coverage = _coverage()
    exclusions = _exclusions()
    details = {(str(x.get("source", "")), str(x.get("id", ""))): x for x in _read_details()}
    old_index: dict[str, int] = {}
    index_path = UNVETTED_DIR / "index.json"
    if only and index_path.exists():
        try:
            old_index = {str(k): int(v) for k, v in json.loads(index_path.read_text(encoding="utf-8")).items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            old_index = {}

    written: dict[str, int] = {}
    merged: dict[str, dict] = {}
    unmatched: list[str] = []
    country_regions = _existing_country_regions()
    stubs_path = UNVETTED_DIR / "stubs.json"
    try:
        old_stubs = json.loads(stubs_path.read_text(encoding="utf-8")) if stubs_path.exists() else []
    except (OSError, json.JSONDecodeError):
        old_stubs = []
    stubs = {str(x.get("people_key")): x for x in old_stubs if isinstance(x, dict) and x.get("people_key")}
    on_map = set(stubs)   # a stub once shown stays, with its writeup, even when its objects go
    targeted_keys = {str(candidate.get("key", "")) for candidate in candidates}
    if only:
        for key in targeted_keys:
            old_stub = stubs.pop(key, None)
            if old_stub:
                old_index.pop(str(old_stub.get("ethnicity_key") or ""), None)
    else:
        stubs = {}
    kept = _screened_keep()
    if not only:
        # Living peoples the screen kept that have no candidate row at all (no
        # museum evidence): a stub with no objects.
        have = {str(c.get("key", "")) for c in candidates}
        candidates = candidates + [_bare_candidate(k) for k in sorted(kept - have)]
    for candidate in candidates:
        matches = _ethnicity_shards(candidate)
        stub = False
        if matches and not candidate.get("objects"):
            continue   # a bare candidate whose people is already on the map adds nothing
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
        if stub and not count and str(candidate.get("key", "")) not in kept | on_map:
            # A culture with nothing to show is put on the map only when the
            # screen kept it (world_peoples.py screen) or it was already there
            # (Catawba, Kam, Hani and Temuan lost their only objects to the BM
            # department check on 2026-10-06); it then shows its writeup and
            # home area without objects.
            stubs.pop(str(candidate.get("key", "")), None)
            continue
        shard = {
            "ethnicity_key": ethkey, "people_key": candidate["key"], "count": count,
            "buckets": buckets, "other": other,
        }
        # Two peoples can share one map point (Lithuanians Q186192 and the
        # medieval Litva tribe Q4263549; Siberians, Arara and Assyrians too).
        # Their objects are merged; the second one used to overwrite the first,
        # which left Lithuanians with 11 of its 402 objects (2026-10-07).
        path = UNVETTED_DIR / f"{ethkey}.json"
        prev = merged.get(ethkey)
        if prev is None and only and path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if candidate["key"] not in (old.get("people_keys") or [old.get("people_key")]):
                prev = old
        if prev:
            shard = _merge_shards(prev, shard)
        merged[ethkey] = shard
        UNVETTED_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(shard, indent=2, ensure_ascii=False), encoding="utf-8")
        count = shard["count"]
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


PLACES_PATH = WORLD_DIR / "stub_places.json"
PLACES_RAW = WORLD_DIR / "stub_places_raw.jsonl"
PLACES_PROMPT = """For each people below, give the latitude and longitude of the centre of its main
traditional homeland (where most of its communities live), as decimal degrees.
One entry per line number.

{lines}"""
PLACES_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["entries"], "properties": {
    "entries": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                           "required": ["i", "lat", "lon"],
                                           "properties": {"i": {"type": "integer"}, "lat": {"type": "number"},
                                                          "lon": {"type": "number"}}}}}}
# Countries whose peoples live far from the mainland polygon (Guam, Greenland,
# Réunion, Easter Island): their homelands skip the distance check.
_OVERSEAS = {"United States", "France", "United Kingdom", "Netherlands", "Denmark", "Chile", "New Zealand",
             "Australia", "Spain", "Portugal", "Ecuador", "Norway"}
# The country polygons leave out some island groups: India's has no Andaman
# and Nicobar Islands, so Onge and Shompen sit ~1,000 km off its border (Great
# Nicobar more). Japan's leaves out Okinawa and the Bonin Islands, Colombia's
# San Andrés (all found by the audit of 2026-10-08).
_OFFSHORE_KM = {"India": 2000, "Japan": 1500, "Colombia": 1000}


def _places() -> dict[str, dict]:
    try:
        return json.loads(PLACES_PATH.read_text(encoding="utf-8")) if PLACES_PATH.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def cmd_places(batch: int = 60) -> None:
    """Homeland points for stub cultures from local Codex, cached in
    data/world/stub_places.json. Checked 2026-10-05 against the 25 stubs with a
    Wikidata coordinate (P625 or P2341): median 143 km off, against 325 km for
    the jittered country centroid (113 km against 325 km on 62).
    Wikidata itself is noisy there (Slovaks put
    8,457 km away), so it is not used as the truth for placement."""
    from folk_patterns.codex_cli import ask
    stubs = json.loads((UNVETTED_DIR / "stubs.json").read_text(encoding="utf-8"))
    cache = _places()
    todo = [s for s in stubs if s["people_key"] not in cache]
    print(f"places: {len(todo)} stubs to place ({len(cache)} cached)", flush=True)
    for n in range(0, len(todo), batch):
        part = todo[n:n + batch]
        lines = "\n".join(f"{i}. {s['ethnicity']} ({s['country']}) [{s['people_key']}]" for i, s in enumerate(part))
        try:
            text = ask(PLACES_PROMPT.format(lines=lines), schema=PLACES_SCHEMA, timeout=900)
        except Exception as exc:  # one failed batch must not stop the run
            print(f"  batch {n}: {type(exc).__name__}", flush=True)
            continue
        with PLACES_RAW.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"batch": [s["people_key"] for s in part], "reply": text}, ensure_ascii=False) + "\n")
        try:
            entries = json.loads(text).get("entries") or []
        except json.JSONDecodeError:
            print(f"  batch {n}: unparsable reply", flush=True)
            continue
        for e in entries:
            if isinstance(e.get("i"), int) and 0 <= e["i"] < len(part):
                cache[part[e["i"]]["people_key"]] = {"lat": float(e["lat"]), "lon": float(e["lon"]), "source": "codex"}
        PLACES_PATH.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  {min(n + batch, len(todo))}/{len(todo)} placed", flush=True)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve", help="cache details for unvetted candidates")
    resolve.add_argument("--only", nargs="*", default=[])
    resolve.add_argument("--limit", type=int, default=0)
    build = sub.add_parser("build", help="write site-facing unvetted shards")
    build.add_argument("--only", nargs="*", default=[])
    sub.add_parser("places", help="homeland points for stub cultures (local Codex, cached)")
    args = ap.parse_args()
    if args.command == "resolve":
        cmd_resolve(args.only, args.limit)
    elif args.command == "places":
        cmd_places()
    else:
        cmd_build(args.only)


if __name__ == "__main__":
    main()
