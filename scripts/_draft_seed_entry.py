"""Draft a seed JSON entry for a new ethnicity via Claude CLI.

Given ethnicity name + country + region, ask claude to produce:
  - homeland: {lat, lon}
  - homeland_place: nearest known city
  - traditions: list of signature textile / motif / art-form terms
  - arch_commons_categories: Wikimedia Commons category names for architecture
  - source_queries: per-museum query variants (colonial demonyms, script variants)

Uses one exemplar (Uzbek entry) as the format reference so claude mirrors it.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _llm import ask_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed"


EXEMPLAR = {
    "name": "Uzbek",
    "homeland": {"lat": 39.65, "lon": 66.96},
    "homeland_place": "Samarkand",
    "traditions": [
        "ikat", "adras", "atlas", "khan-atlas", "suzani",
        "chust duppi", "zardozi", "chapan", "bekasab",
        "Rishtan glaze", "kashin tile", "muqarnas",
        "gul motif", "Bukharan gold embroidery",
    ],
    "arch_commons_categories": [
        "Bibi-Khanym Mosque", "Shah-i-Zinda", "Kalyan minaret",
        "Registan", "Ulugh Beg Madrasah (Samarkand)",
        "Ark of Bukhara", "Chor Minor",
    ],
    "source_queries": {
        "cleveland": ["Uzbek", "Uzbekistan", "Bukhara", "Samarkand"],
    },
}


PROMPT = """You are drafting a seed JSON entry for an ethnicity in a folk-culture atlas.

The schema mirrors this Uzbek example EXACTLY:

{exemplar}

Now produce the equivalent for:
- Ethnicity: {name}
- Country: {country}
- Region: {region}

Rules:
- homeland.lat/lon should be a real coordinate near the ethnicity's traditional heartland (not the country capital unless it IS the heartland).
- homeland_place: the nearest well-known city.
- traditions: 8-25 items. Prefer distinctive local ethnonyms for textile / motif / dress / architecture / metalwork. Avoid generic English words.
- arch_commons_categories: real Wikimedia Commons category names (exact spelling). Only include categories that actually exist for this culture's built heritage. If the culture is nomadic and has no signature architecture, use an empty list.
- source_queries.cleveland: list ethnonyms and colonial demonyms that a museum catalog would use (e.g. "Fula" and "Peul" for Fulani; "Burmese" for Bamar).

Return ONLY a single JSON object, no code fences, no prose.
"""


def draft(name: str, country: str, region: str) -> dict:
    prompt = PROMPT.format(
        exemplar=json.dumps(EXEMPLAR, indent=2, ensure_ascii=False),
        name=name, country=country, region=region,
    )
    obj = ask_json(prompt)
    if not isinstance(obj, dict):
        raise RuntimeError(f"LLM returned {type(obj).__name__}, expected dict")
    # Force the name to match request (LLM sometimes changes case/punctuation)
    obj["name"] = name
    return obj


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--country", required=True)
    ap.add_argument("--region", required=True, help="region slug matching data/seed/<region>.json")
    ap.add_argument("--out", help="write JSON to this path instead of stdout")
    args = ap.parse_args()

    entry = draft(args.name, args.country, args.region)
    text = json.dumps(entry, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
