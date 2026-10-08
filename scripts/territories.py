"""Approximate home territory per culture, shaded on the map on hover and click.

    python scripts/territories.py fetch     # source polygons into data/raw/territories/
    python scripts/territories.py ids       # culture -> Wikidata Q-id -> glottocodes
    python scripts/territories.py match     # candidate polygons per culture
    python scripts/territories.py judge     # local Codex picks polygons or draws an ellipse
    python scripts/territories.py build     # data/territories/<key>.json for the site

Sources (searched 2026-10-05, see tools/knowledge base/searches/):
  asher  Glottography asher2007world, traditional speaker areas from Asher &
         Moseley 2007 "Atlas of the World's Languages", 4,500 polygons keyed by
         glottocode, CC BY 4.0.
  nl     Native Land Digital territories, 2,057 polygons, mostly the Americas
         and Oceania, CC0; the full GeoJSON downloaded without an API key.
  greg   GREG (Weidmann, Rod & Cederman 2010), 928 groups from the Atlas
         Narodov Mira 1964, cite-only.
A culture without a fitting polygon gets an ellipse from local Codex.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlencode

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from folk_patterns.util import DATA_DIR  # noqa: E402

RAW = DATA_DIR / "raw" / "territories"
WORK = DATA_DIR / "world"
IDS_PATH = WORK / "territory_ids.json"
UA = {"User-Agent": "folk-patterns/0.1 (https://folk-patterns.vercel.app)"}
SOURCES = {
    "asher.geojson": "https://raw.githubusercontent.com/Glottography/asher2007world/main/cldf/traditional/languages.geojson",
    "nl.geojson": "https://native-land.ca/api/polygons/geojson/territories",
    "GREG.zip": "https://icr.ethz.ch/data/greg/GREG.zip",
}


def cmd_fetch() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        path = RAW / name
        if path.exists() and path.stat().st_size > 0:
            print(f"{name}: cached", flush=True)
            continue
        with httpx.stream("GET", url, headers=UA, timeout=300, follow_redirects=True) as r:
            r.raise_for_status()
            with path.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        print(f"{name}: {path.stat().st_size:,} bytes", flush=True)
    if not (RAW / "GREG.shp").exists():
        import zipfile
        zipfile.ZipFile(RAW / "GREG.zip").extractall(RAW)


def _cultures() -> list[dict]:
    """Every culture on the globe with its key, name, country and lat/lon."""
    return json.loads((DATA_DIR / "globe.json").read_text(encoding="utf-8"))["points"]


def cmd_ids() -> None:
    """Q-id from the stub's people_key or the vetted shard's Wikipedia title,
    then glottocodes of the people's languages (P103 native language, P2936
    language used) and of the item itself (P1394)."""
    cache = json.loads(IDS_PATH.read_text(encoding="utf-8")) if IDS_PATH.exists() else {}
    stubs = {s["ethnicity_key"]: s["people_key"]
             for s in json.loads((DATA_DIR / "unvetted" / "stubs.json").read_text(encoding="utf-8"))}
    titles = {}
    for c in _cultures():
        key = c["key"]
        if key in cache:
            continue
        if key in stubs:
            cache[key] = {"qid": stubs[key]}
            continue
        shard = json.loads((DATA_DIR / "ethnicities" / f"{key}.json").read_text(encoding="utf-8"))
        if shard.get("wikipedia_title"):
            titles[shard["wikipedia_title"]] = key
        else:
            cache[key] = {"qid": None}
    client = httpx.Client(headers=UA, timeout=60)
    names = list(titles)
    for n in range(0, len(names), 50):
        part = names[n:n + 50]
        r = client.get("https://en.wikipedia.org/w/api.php", params={
            "action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "redirects": 1,
            "titles": "|".join(part), "format": "json"}).json()["query"]
        alias = {x["from"]: x["to"] for x in r.get("normalized", []) + r.get("redirects", [])}
        qids = {p["title"]: p.get("pageprops", {}).get("wikibase_item") for p in r["pages"].values()}
        for t in part:
            t2 = alias.get(alias.get(t, t), alias.get(t, t))
            cache[titles[t]] = {"qid": qids.get(t2)}
    todo = [k for k, v in cache.items() if v.get("qid") and "glottocodes" not in v]
    by_q: dict[str, list[str]] = {}
    for k in todo:
        by_q.setdefault(cache[k]["qid"], []).append(k)
    qs = list(by_q)
    for n in range(0, len(qs), 200):
        part = qs[n:n + 200]
        query = ("SELECT ?p ?g WHERE { VALUES ?p {" + " ".join(f"wd:{q}" for q in part) + "} "
                 "{ ?p wdt:P1394 ?g } UNION { ?p (wdt:P103|wdt:P2936) ?l . ?l wdt:P1394 ?g } }")
        for attempt in range(5):
            r = client.post("https://query.wikidata.org/sparql", data={"query": query},
                            headers={"Accept": "application/sparql-results+json"})
            if r.status_code == 200 and r.content:
                break
            print(f"  SPARQL {r.status_code}, retrying", flush=True)  # 429 / timeout page, not JSON
            time.sleep(int(r.headers.get("Retry-After", 0) or 0) or 10 * (attempt + 1))
        rows = r.json()["results"]["bindings"]
        found: dict[str, set[str]] = {}
        for row in rows:
            found.setdefault(row["p"]["value"].rsplit("/", 1)[1], set()).add(row["g"]["value"])
        for q in part:
            for k in by_q[q]:
                cache[k]["glottocodes"] = sorted(found.get(q, ()))
        time.sleep(1)
        print(f"  {min(n + 200, len(qs))}/{len(qs)} Q-ids queried", flush=True)
    IDS_PATH.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    with_q = sum(bool(v.get("qid")) for v in cache.values())
    with_g = sum(bool(v.get("glottocodes")) for v in cache.values())
    print(f"{len(cache)} cultures, {with_q} with a Q-id, {with_g} with a glottocode", flush=True)


CANDS_PATH = WORK / "territory_candidates.jsonl"


def _stems(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()
    out = {s}
    for suf in ("people", "pople", "tribe", "nation", "ians", "ans", "ese", "ns", "s", "is", "es"):
        if s.endswith(suf) and len(s) - len(suf) >= 3:
            out.add(s[:-len(suf)].strip())
    return {x for x in out if len(x) >= 3}


def _shapes() -> list[dict]:
    """One entry per (source, name): merged geometry plus the names to match on."""
    from shapely.geometry import shape
    from shapely.ops import unary_union
    groups: dict[tuple[str, str], dict] = {}

    def add(src: str, name: str, geom, aliases: list[str], code: str = "") -> None:
        g = groups.setdefault((src, name), {"source": src, "name": name, "geoms": [], "aliases": set(), "codes": set()})
        g["geoms"].append(geom)
        g["aliases"].update(a for a in aliases if a.strip())
        if code:
            g["codes"].add(code)

    for f in json.loads((RAW / "asher.geojson").read_text(encoding="utf-8"))["features"]:
        p = f["properties"]
        add("asher", p["title"], shape(f["geometry"]), [p["title"]], p.get("cldf:languageReference") or "")
    for f in json.loads((RAW / "nl.geojson").read_text(encoding="utf-8"))["features"]:
        if f.get("geometry"):
            nm = f["properties"]["Name"].strip()
            add("nl", nm, shape(f["geometry"]), re.split(r"[(),;/]", nm))
    import shapefile
    r = shapefile.Reader(str(RAW / "GREG.shp"), encoding="cp1252")
    for sr in r.iterShapeRecords():
        geom = shape(sr.shape.__geo_interface__)
        for k in ("G1", "G2", "G3"):
            if sr.record[k + "SHORTNAM"]:
                add("greg", sr.record[k + "SHORTNAM"], geom,
                    [sr.record[k + "SHORTNAM"]] + re.split(r"[(),;/]| and ", sr.record[k + "LONGNAM"]))
    out = []
    for g in groups.values():
        geom = unary_union([x.buffer(0) for x in g["geoms"]])
        if geom.is_empty:
            continue
        out.append({**g, "geom": geom, "stems": set().union(*(_stems(a) for a in g["aliases"]))})
    return out


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def cmd_match() -> None:
    """Candidates per culture: a polygon whose glottocode is one of the
    people's languages, or whose name matches the culture's name. Contact
    languages come in through Wikidata too (Votes -> Estonian, Russian), so
    nothing here is final; `judge` decides."""
    ids = json.loads(IDS_PATH.read_text(encoding="utf-8"))
    shapes = _shapes()
    by_stem: dict[str, list[int]] = {}
    by_code: dict[str, list[int]] = {}
    for i, s in enumerate(shapes):
        for st in s["stems"]:
            by_stem.setdefault(st, []).append(i)
        for c in s["codes"]:
            by_code.setdefault(c, []).append(i)
    padded_names = [" " + " | ".join(" ".join(sorted(_stems(a), key=len)[-1:]) for a in s["aliases"]) + " "
                    for s in shapes]
    n_with = 0
    with CANDS_PATH.open("w", encoding="utf-8") as f:
        for c in _cultures():
            hits: dict[int, str] = {}
            for st in _stems(c["ethnicity"]):
                for i in by_stem.get(st, ()):
                    hits[i] = "name"
            # the culture's name as whole words inside a longer polygon name
            # ("Mescalero" in "Mescalero Apache")
            whole = " " + " ".join(sorted(_stems(c["ethnicity"]), key=len)[-1:]) + " "
            if len(whole) > 6:
                for i, padded in enumerate(padded_names):
                    if whole in padded:
                        hits.setdefault(i, "name")
            for g in ids.get(c["key"], {}).get("glottocodes", []):
                for i in by_code.get(g, ()):
                    hits.setdefault(i, "language")
            cands = []
            for i, why in hits.items():
                s = shapes[i]
                pt = s["geom"].representative_point()
                minx, miny, maxx, maxy = s["geom"].bounds
                cands.append({"i": i, "source": s["source"], "name": s["name"], "via": why,
                              "center": [round(pt.y, 2), round(pt.x, 2)],
                              "km_from_marker": round(_km((c["lat"], c["lon"]), (pt.y, pt.x))),
                              "extent_km": round(_km((miny, minx), (maxy, maxx)))})
            n_with += bool(cands)
            f.write(json.dumps({"key": c["key"], "ethnicity": c["ethnicity"], "country": c.get("country"),
                                "lat": c["lat"], "lon": c["lon"], "candidates": cands}, ensure_ascii=False) + "\n")
    print(f"{len(shapes)} source shapes; {n_with} cultures with at least one candidate", flush=True)


VERDICTS_PATH = WORK / "territory_verdicts.json"
VERDICTS_RAW = WORK / "territory_verdicts_raw.jsonl"
JUDGE_PROMPT = """You map where each folk culture below traditionally lives. Each has a map
marker (lat, lon) and numbered candidate polygons from three sources: "asher"
(language speaker areas, Atlas of the World's Languages 2007), "nl" (Native Land
territories) and "greg" (ethnic group areas, Soviet atlas 1964). A candidate came
from a name match or from a language Wikidata lists for the people; the latter
includes contact and national languages (Votes -> Russian), which are wrong.

For each culture:
- picks: the candidate numbers whose polygons together show the culture's own
  traditional area. Pick a candidate only if it is THIS people (or its own
  language), not a neighbour, a broader nation, a national language, a much
  larger family, or a far-away diaspora. Several are fine when they are the same
  people (a language polygon plus its Native Land territory). Empty when none fit.
- ellipse: your own best approximation of the area regardless of picks: centre
  lat/lon, semi-axes rx_km (east-west) and ry_km (north-south), and rotation
  angle_deg (0 = axes east-west/north-south, positive = counter-clockwise).
  Size it to the real area: a small island or valley people tens of km, a large
  nation hundreds.
Answer one entry per culture number.

{lines}"""
JUDGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["entries"], "properties": {
    "entries": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["n", "picks", "lat", "lon", "rx_km", "ry_km", "angle_deg"],
        "properties": {"n": {"type": "integer"}, "picks": {"type": "array", "items": {"type": "integer"}},
                       "lat": {"type": "number"}, "lon": {"type": "number"}, "rx_km": {"type": "number"},
                       "ry_km": {"type": "number"}, "angle_deg": {"type": "number"}}}}}}


def _judge_lines(rows: list[dict]) -> str:
    out = []
    for n, r in enumerate(rows):
        out.append(f"{n}. {r['ethnicity']} ({r['country']}), marker {r['lat']:.2f}, {r['lon']:.2f}")
        for j, c in enumerate(r["candidates"]):
            out.append(f"   [{j}] {c['source']}: {c['name']} (via {c['via']}; centre {c['center'][0]}, {c['center'][1]}, "
                       f"{c['km_from_marker']} km from marker, spans {c['extent_km']} km)")
        if not r["candidates"]:
            out.append("   (no candidates)")
    return "\n".join(out)


def cmd_judge(batch: int, limit: int, workers: int, only: list[str]) -> None:
    """Local Codex picks the candidate polygons that are the culture's own and
    draws an ellipse for every culture; raw replies go to territory_verdicts_raw.jsonl."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from folk_patterns.codex_cli import ask
    rows = [json.loads(l) for l in CANDS_PATH.read_text(encoding="utf-8").splitlines()]
    verdicts = json.loads(VERDICTS_PATH.read_text(encoding="utf-8")) if VERDICTS_PATH.exists() else {}
    todo = [r for r in rows if r["key"] not in verdicts and (not only or r["ethnicity"] in only)]
    if limit:
        todo = todo[:limit]
    print(f"judge: {len(todo)} cultures ({len(verdicts)} cached)", flush=True)
    lock = threading.Lock()

    def run(part: list[dict]) -> None:
        try:
            text = ask(JUDGE_PROMPT.format(lines=_judge_lines(part)), schema=JUDGE_SCHEMA, timeout=1200)
        except Exception as exc:  # one failed batch must not stop the run
            print(f"  batch at {part[0]['ethnicity']}: {type(exc).__name__}", flush=True)
            return
        with lock:
            with VERDICTS_RAW.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"batch": [r["key"] for r in part], "reply": text}, ensure_ascii=False) + "\n")
            try:
                entries = json.loads(text)["entries"]
            except (json.JSONDecodeError, KeyError):
                print("  unparsable reply", flush=True)
                return
            for e in entries:
                if not (isinstance(e.get("n"), int) and 0 <= e["n"] < len(part)):
                    continue
                r = part[e["n"]]
                picks = [r["candidates"][j]["i"] for j in e["picks"] if 0 <= j < len(r["candidates"])]
                verdicts[r["key"]] = {"picks": picks, "ellipse": {k: e[k] for k in ("lat", "lon", "rx_km", "ry_km", "angle_deg")}}
            VERDICTS_PATH.write_text(json.dumps(verdicts, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"  {len(verdicts)} judged", flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(run, [todo[i:i + batch] for i in range(0, len(todo), batch)]))


OUT_DIR = DATA_DIR / "territories"
CREDITS = {"asher": "Asher & Moseley 2007 via Glottography (CC BY 4.0)",
           "nl": "Native Land Digital (CC0)",
           "greg": "GREG, Weidmann, Rød & Cederman 2010"}


def _ellipse(e: dict, n: int = 64):
    import math
    from shapely.geometry import Polygon
    lat0, lon0 = e["lat"], e["lon"]
    rx, ry = max(e["rx_km"], 5), max(e["ry_km"], 5)
    a = math.radians(e.get("angle_deg") or 0)
    pts = []
    for k in range(n):
        t = 2 * math.pi * k / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        x, y = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
        pts.append((lon0 + x / (111.32 * max(math.cos(math.radians(lat0)), 0.05)), max(-89.9, min(89.9, lat0 + y / 110.57))))
    return Polygon(pts)


def cmd_build() -> None:
    """One GeoJSON Feature per culture in data/territories/<key>.json: the
    union of the picked polygons, simplified, or else the Codex ellipse."""
    from shapely.geometry import mapping
    from shapely.ops import unary_union
    verdicts = json.loads(VERDICTS_PATH.read_text(encoding="utf-8"))
    shapes = _shapes()
    OUT_DIR.mkdir(exist_ok=True)
    for old in OUT_DIR.glob("*.json"):
        old.unlink()
    stub_people = {s["ethnicity_key"]: s["people_key"]
                   for s in json.loads((DATA_DIR / "unvetted" / "stubs.json").read_text(encoding="utf-8"))}
    places_path = WORK / "stub_places.json"
    places = json.loads(places_path.read_text(encoding="utf-8"))
    kinds = {"polygon": 0, "ellipse": 0}
    total = 0
    for c in _cultures():
        v = verdicts.get(c["key"])
        if not v:
            continue
        marker = (c["lat"], c["lon"])
        ellipse = _ellipse(v["ellipse"])
        picked = [shapes[i] for i in v["picks"] if i < len(shapes)]
        geom = None
        if picked:
            geom = unary_union([s["geom"] for s in picked])
            if _off(geom, marker) > MAX_OFF_KM:
                if _off(ellipse, marker) <= MAX_OFF_KM:
                    # the polygon is a part elsewhere (Dayak -> Malayic Dayak only)
                    print(f"  {c['ethnicity']}: polygon {_off(geom, marker):.0f} km off, ellipse used", flush=True)
                    geom = None
                elif str(places.get(stub_people.get(c["key"], ""), {}).get("source", "")).startswith("audit"):
                    # a point checked by hand (scripts/audit_places.py) outranks
                    # the old polygon: Taz, Lom and the Danes sat at a wrong one
                    print(f"  {c['ethnicity']}: polygon {_off(geom, marker):.0f} km off an audited marker, ellipse used", flush=True)
                    geom = None
                elif c["key"] in stub_people:
                    # polygon and ellipse agree, the marker is the odd one out
                    # (a Codex homeland point: Tapirapé 4 degrees too far north)
                    pt = geom.representative_point()
                    places[stub_people[c["key"]]] = {"lat": round(pt.y, 3), "lon": round(pt.x, 3), "source": "territory"}
                    print(f"  {c['ethnicity']}: marker {_off(geom, marker):.0f} km off, moved to its polygon", flush=True)
                else:
                    print(f"  {c['ethnicity']}: vetted marker {_off(geom, marker):.0f} km off its polygon, check the seed", flush=True)
        if geom is not None:
            minx, miny, maxx, maxy = geom.bounds
            geom = geom.simplify(max(0.01, min(0.1, max(maxx - minx, maxy - miny) / 200)), preserve_topology=True)
            kind, sources = "polygon", sorted({CREDITS[s["source"]] for s in picked})
        else:
            if _off(ellipse, marker) > MAX_OFF_KM:
                print(f"  {c['ethnicity']}: ellipse {_off(ellipse, marker):.0f} km off, centred on the marker", flush=True)
                ellipse = _ellipse({**v["ellipse"], "lat": marker[0], "lon": marker[1]})
            geom, kind, sources = ellipse, "ellipse", ["approximate area estimated by Codex"]
        kinds[kind] += 1
        feat = {"type": "Feature", "properties": {"kind": kind, "sources": sources}, "geometry": mapping(geom)}
        text = json.dumps(feat, separators=(",", ":"))
        text = re.sub(r"(\d+\.\d{3})\d+", r"\1", text)
        (OUT_DIR / f"{c['key']}.json").write_text(text, encoding="utf-8")
        total += len(text)
    places_path.write_text(json.dumps(places, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{kinds['polygon']} polygons, {kinds['ellipse']} ellipses, {total / 1e6:.1f} MB in {OUT_DIR}", flush=True)
    print("moved markers take effect after unvetted.py build, build_index.py and another territories.py build", flush=True)


# An area must sit around its marker: farther than this from the marker, a
# polygon gives way to the ellipse, or the marker moves (see cmd_build).
MAX_OFF_KM = 150


def _off(geom, marker: tuple[float, float]) -> float:
    """km from the marker to the area; 0 inside."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    pt = Point(marker[1], marker[0])
    if geom.contains(pt):
        return 0.0
    q = nearest_points(geom, pt)[0]
    return _km(marker, (q.y, q.x))


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["fetch", "ids", "match", "judge", "build"])
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only", nargs="*", default=[])
    a = ap.parse_args()
    if a.command == "judge":
        cmd_judge(a.batch, a.limit, a.workers, a.only)
    else:
        {"fetch": cmd_fetch, "ids": cmd_ids, "match": cmd_match, "build": cmd_build}[a.command]()


if __name__ == "__main__":
    main()
