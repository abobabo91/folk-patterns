"""Check every culture on the map: is its name a people's name, is the country
right, and does its point lie in that people's homeland. Local Codex judges in
batches; this script supplies the facts it cannot see (which country polygon
the point falls in, the Wikipedia article the page links).

    python scripts/audit_places.py facts            # -> work/audit/facts.jsonl
    FOLK_LLM_BACKEND=codex python scripts/audit_places.py judge [--limit N] [--only KEY ...]
                                                    # -> work/audit/verdicts.json, raw.jsonl
    python scripts/audit_places.py report           # flagged cultures, one line each

The verdicts are advice: fixes are applied by hand (classified.json,
stub_places.json, the seeds), since the judge also errs.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "work" / "audit"
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))


def _point_country(lat: float, lon: float, features: list[dict]) -> str:
    def inside(ring: list, x: float, y: float) -> bool:
        c = False
        for i in range(len(ring)):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[i - 1][0], ring[i - 1][1]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                c = not c
        return c
    for f in features:
        g = f.get("geometry") or {}
        polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates") or []
        for poly in polys:
            if poly and inside(poly[0], lon, lat):
                return str((f.get("properties") or {}).get("name") or "")
    return "(sea or unmapped)"


def cmd_facts() -> None:
    geo = json.loads((REPO / "site" / "public" / "data" / "world-countries.geojson").read_text(encoding="utf-8"))["features"]
    stubs = {s["ethnicity_key"]: s for s in json.loads((REPO / "data" / "unvetted" / "stubs.json").read_text(encoding="utf-8"))}
    wd = {r["qid"]: r for r in json.loads((REPO / "data" / "world" / "wikidata.json").read_text(encoding="utf-8"))}
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(OUT / "facts.jsonl", "w", encoding="utf-8") as f:
        for p in sorted(glob.glob(str(REPO / "data" / "ethnicities" / "*.json"))):
            e = json.loads(Path(p).read_text(encoding="utf-8"))
            h = e.get("homeland") or {}
            qid = (stubs.get(e["key"]) or {}).get("people_key") or ""
            f.write(json.dumps({
                "key": e["key"], "name": e["ethnicity"], "country": e["country"], "region": e["region"],
                "lat": h.get("lat"), "lon": h.get("lon"), "homeland_place": e.get("homeland_place"),
                "point_in": _point_country(h["lat"], h["lon"], geo) if h else "",
                "wikidata": qid, "wikidata_label": (wd.get(qid) or {}).get("label", ""),
                "wikipedia_title": e.get("wikipedia_title") or "", "stub": bool(e.get("unvetted_only")),
                "extinct": bool(e.get("extinct")),
            }, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} cultures -> {OUT / 'facts.jsonl'}")


PROMPT = """You check the cultures of a world atlas of folk culture. Each entry is one
map marker for a people. For each, judge from your own knowledge:

- name: is it a correct, recognisable English name for that people? Flag a
  misspelling, a non-people (a dish, a place, a language only, a religion), or a
  name that is not the usual one. Small style differences ("Otoe tribe") are ok.
- country: is it a country where this people lives (or lived, if extinct)?
- point: lat/lon is the marker; "point_in" is the country polygon it falls in
  ("(sea or unmapped)" = in the sea, or a tiny island state). Is the point inside
  or near the people's homeland (within ~300 km for small peoples; anywhere in
  the main homeland for large nations)? A point at sea is wrong unless the
  people live on small islands there.
- wikipedia_title, when given: is it the article about this people?
- duplicate: does another entry in THIS batch denote the same people?

Be strict but only flag what you are confident is wrong. Give fixes: a better
name, the right country, an approximate homeland lat/lon, the right article.

Entries:
{entries}

Reply with JSON only: {{"entries": [{{"key": "...", "ok": true|false, "problems": ["name"|"country"|"point"|"wikipedia"|"duplicate"], "fix_name": "", "fix_country": "", "fix_lat": null, "fix_lon": null, "fix_wikipedia": "", "duplicate_of": "", "reason": "..."}}]}}, one per entry, same keys."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["entries"], "properties": {"entries": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["key", "ok", "problems", "fix_name", "fix_country", "fix_lat", "fix_lon", "fix_wikipedia", "duplicate_of", "reason"],
        "properties": {"key": {"type": "string"}, "ok": {"type": "boolean"},
                       "problems": {"type": "array", "items": {"type": "string", "enum": ["name", "country", "point", "wikipedia", "duplicate"]}},
                       "fix_name": {"type": "string"}, "fix_country": {"type": "string"},
                       "fix_lat": {"type": ["number", "null"]}, "fix_lon": {"type": ["number", "null"]},
                       "fix_wikipedia": {"type": "string"}, "duplicate_of": {"type": "string"}, "reason": {"type": "string"}}}}}}


def cmd_judge(limit: int, only: list[str], workers: int) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from folk_patterns.codex_cli import ask
    facts = [json.loads(l) for l in (OUT / "facts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    vp, rp = OUT / "verdicts.json", OUT / "raw.jsonl"
    cache = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else {}
    todo = [f for f in facts if (f["key"] in only if only else f["key"] not in cache)]
    # batches by country, so duplicates inside a country are seen together
    todo.sort(key=lambda f: (f["country"], f["name"]))
    if limit:
        todo = todo[:limit]
    batches = [todo[i:i + 25] for i in range(0, len(todo), 25)]
    print(f"judge: {len(todo)} cultures in {len(batches)} batches ({len(cache)} cached)", flush=True)

    def one(batch: list[dict]) -> list[dict]:
        entries = "\n".join(json.dumps({k: f[k] for k in ("key", "name", "country", "lat", "lon", "point_in", "wikipedia_title", "extinct")},
                                       ensure_ascii=False) for f in batch)
        reply = ask(PROMPT.format(entries=entries), schema=SCHEMA)
        with open(rp, "a", encoding="utf-8") as f:
            f.write(json.dumps({"keys": [b["key"] for b in batch], "reply": reply}, ensure_ascii=False) + "\n")
        try:
            return json.loads(reply).get("entries") or []
        except (TypeError, ValueError, AttributeError):
            print(f"  ! unparsable reply for {batch[0]['key']}", flush=True)
            return []

    with ThreadPoolExecutor(workers) as ex:
        for i, got in enumerate(ex.map(one, batches), 1):
            for g in got:
                cache[g["key"]] = g
                if not g["ok"]:
                    print(f'  {",".join(g["problems"]):18} {g["key"][:50]:50} {g["reason"][:110]}', flush=True)
            vp.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
            print(f"batch {i}/{len(batches)} done ({len(cache)} judged)", flush=True)


def cmd_report() -> None:
    v = json.loads((OUT / "verdicts.json").read_text(encoding="utf-8"))
    facts = {json.loads(l)["key"]: json.loads(l) for l in (OUT / "facts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    bad = [g for g in v.values() if not g["ok"]]
    from collections import Counter
    print(f"{len(v)} judged, {len(bad)} flagged:", Counter(p for g in bad for p in g["problems"]))
    for g in sorted(bad, key=lambda g: g["key"]):
        f = facts.get(g["key"], {})
        print(f'{g["key"]} | {f.get("name")} | {f.get("country")} @ {f.get("lat")},{f.get("lon")} in {f.get("point_in")} | '
              f'{",".join(g["problems"])} | {g["fix_name"]} {g["fix_country"]} {g["fix_lat"]},{g["fix_lon"]} {g["fix_wikipedia"]} {g["duplicate_of"]} | {g["reason"]}')


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["facts", "judge", "report"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    {"facts": cmd_facts, "judge": lambda: cmd_judge(a.limit, a.only, a.workers), "report": cmd_report}[a.step]()
