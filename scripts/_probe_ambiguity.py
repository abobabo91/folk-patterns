"""Pre-scrape ambiguity probe for a new ethnonym.

Search Europeana with the bare ethnonym, hand the top titles to claude, ask
which look off-topic. If claude flags contamination, it drafts a reject regex
that the user can review and add to `europeana._AMBIGUOUS_ETHNONYM_REJECT`.

Cheap: one Europeana search + one LLM call per new ethnicity.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from folk_patterns.util import RateLimitedClient
from folk_patterns.museums.europeana import search, _first
from _llm import ask_json


PROMPT = """You are reviewing a Europeana search-result set to detect ethnonym-collision risks.

Target ethnicity: {name}
Target country: {country}
Region context: {region}

Here are the top {n} search results for the bare-word query "{name}":
{results}

Question: which titles are OBVIOUSLY NOT about the {name} people of {country}?
Common collision types:
- Saint-name architecture (San Marco, San Lorenzo…) when searching a short ethnonym like "San"
- Empire / country names (China, Chinese) when the ethnonym is a substring
- Author pen-names (French cartoonist "Cham")
- Language-collision grammatical forms (Estonian "kalaga" = "with fish", matches Burmese "kalaga" wall-hanging)
- Commercial ephemera (candy wrappers, matchbox labels)
- Academic paper title pages

Return a JSON object with:
  "off_topic_indices": [list of indices from the numbered list above that are clearly wrong]
  "reject_regex": "a single Python regex pattern that would reject the flagged records by matching their titles (or empty string if none flagged)"
  "reasoning": "one sentence per class of collision found"
"""


def probe(name: str, country: str, region: str, top_n: int = 20) -> dict:
    client = RateLimitedClient(min_interval_s=0.35)
    try:
        j = search(client, name, rows=top_n)
    finally:
        client.close()

    items = j.get("items", [])[:top_n]
    if not items:
        return {"off_topic_indices": [], "reject_regex": "", "reasoning": "No search results — nothing to probe."}

    listed = []
    for i, it in enumerate(items):
        title = _first(it.get("title")) or ""
        provider = _first(it.get("dataProvider")) or _first(it.get("provider")) or ""
        listed.append(f"  {i}: {title[:100]}  [provider: {provider}]")

    prompt = PROMPT.format(
        name=name, country=country, region=region,
        n=len(items), results="\n".join(listed),
    )
    result = ask_json(prompt)
    result["items"] = [
        {"i": i, "title": _first(it.get("title")) or "",
         "provider": _first(it.get("dataProvider")) or _first(it.get("provider")) or ""}
        for i, it in enumerate(items)
    ]
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--country", required=True)
    ap.add_argument("--region", required=True)
    ap.add_argument("--top-n", type=int, default=20)
    args = ap.parse_args()

    result = probe(args.name, args.country, args.region, args.top_n)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
