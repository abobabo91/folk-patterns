"""Draft a whole new region seed file via Claude CLI.

Given a region slug + a list of countries + a short natural-language
description, ask claude to produce a seed JSON with:
  - region: the display name
  - region_places: {place_to_country, reject_places, signature_traditions}
  - countries: [{country, met_queries, majority_ethnicity, met_gate_tokens,
                 ethnicities: []}]

Empty `ethnicities: []` because the operator adds ethnicities one at a time
with `add_culture.py`, which fills in the details per-culture.
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


EXEMPLAR_MENA_PLACES = {
    "place_to_country": {
        "iran": "Iran", "tehran": "Iran", "isfahan": "Iran",
        "shiraz": "Iran", "tabriz": "Iran", "morocco": "Morocco",
        "marrakech": "Morocco", "fes": "Morocco", "cairo": "Egypt",
        "alexandria": "Egypt", "istanbul": "Turkey", "anatolia": "Turkey",
        "middle east": "_regional", "north africa": "_regional",
    },
    "reject_places": [
        "china", "japan", "india", "spain", "france",
        "italy", "greece", "russia",
    ],
    "signature_traditions": {
        "iznik": "Turkey", "safavid": "Iran", "qajar": "Iran",
        "berber jewelry": "Morocco", "assiut": "Egypt",
    },
}


PROMPT = """You are producing a seed JSON file for a folk-culture atlas region.

An existing region (MENA) has this `region_places` block as reference:
{exemplar}

Now produce the analogous seed file for:
- Region slug: {slug}
- Display name: {name}
- Included countries: {countries}
- Extra context: {context}

Return a single JSON object with this exact top-level shape:
{{
  "region": "{name}",
  "region_places": {{
    "place_to_country": {{ ... }},
    "reject_places": [ ... ],
    "signature_traditions": {{ ... }}
  }},
  "countries": [
    {{
      "country": "<country name>",
      "met_queries": [ "<queries Met catalog might use>" ],
      "majority_ethnicity": null,
      "met_gate_tokens": [ "<historical umbrella terms>" ],
      "ethnicities": []
    }},
    ...
  ]
}}

Rules for region_places:
- place_to_country: 20-40 lowercase place tokens (cities, historical regions,
  colonial names) → target country. Include the country name itself, its capital,
  2-4 major cities/regions, and historically important cities. Add the region
  display name (lowercase) as "_regional" so records placed only at region
  granularity land in a _regional bucket.
- reject_places: 10-20 lowercase place tokens for adjacent regions we don't
  want catching cross-listed records (China/Japan for a SE Asia region,
  Europe/Africa for a MENA region, etc.).
- signature_traditions: 5-15 tokens for objects so distinctively one culture's
  that they can be accepted even with a vague place field.

Rules for countries:
- met_queries: 3-6 terms the Met catalog would use (country name, historical
  polity names, capital city). NOT ethnonyms.
- met_gate_tokens: 3-6 umbrella terms for accepting Met search hits
  (e.g. "Bactria", "Sogdian" for Uzbekistan).
- majority_ethnicity: null for now — add_culture.py sets it when the first
  ethnicity is added.
- ethnicities: [] — populated later.

Return ONLY the JSON object. No prose, no fences.
"""


def draft(slug: str, name: str, countries: list[str], context: str = "") -> dict:
    prompt = PROMPT.format(
        slug=slug, name=name,
        countries=", ".join(countries),
        context=context or "(none)",
        exemplar=json.dumps(EXEMPLAR_MENA_PLACES, indent=2, ensure_ascii=False),
    )
    obj = ask_json(prompt, timeout=600)
    if not isinstance(obj, dict):
        raise RuntimeError(f"LLM returned {type(obj).__name__}, expected dict")
    return obj


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, help="Region slug (e.g. 'latin_america')")
    ap.add_argument("--name", required=True, help="Display name (e.g. 'Latin America')")
    ap.add_argument("--countries", required=True, help="Comma-separated country list")
    ap.add_argument("--context", default="", help="Extra context for the LLM (optional)")
    ap.add_argument("--out", help="Write to this path instead of stdout")
    args = ap.parse_args()

    countries = [c.strip() for c in args.countries.split(",") if c.strip()]
    seed = draft(args.slug, args.name, countries, args.context)
    text = json.dumps(seed, indent=2, ensure_ascii=False)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        default = SEED / f"{args.slug}.json"
        default.write_text(text, encoding="utf-8")
        print(f"wrote {default}", file=sys.stderr)


if __name__ == "__main__":
    main()
