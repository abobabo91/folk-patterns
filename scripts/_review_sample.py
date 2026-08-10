"""Post-scrape sample review for a newly-scraped ethnicity.

Pick N random records from an ethnicity's library folder, hand titles +
providers to claude, ask which look wrong. If misroutes found, claude drafts
a reject pattern the user can commit.

Runs after scrape_all completes for a new culture. Catches contamination
classes we haven't seen before (new authors, new place-name collisions, new
academic repositories) without waiting for a human to visually audit.
"""
from __future__ import annotations
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _llm import ask_json

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"


PROMPT = """You are auditing a folk-culture atlas library for records that were
incorrectly attributed during scraping.

Target ethnicity: {name}
Target country: {country}

Here are {n} random records currently filed under this ethnicity:
{records}

For each record, decide: does this look like it's actually about the {name}
people of {country}?

Common contamination signals:
- Provider is from a country unrelated to {country} AND the title is in that
  provider's local language (not translated)
- Title describes something in a foreign geographic context (Italian saints,
  Spanish maps, Estonian fish scenes)
- Title looks like an auction catalog / academic paper / commercial packaging
- Object type is a coin, banknote, or other numismatic item unrelated to folk art
- Title mentions a totally different culture (Khmer temple filed as Cham, etc.)

Return a JSON object with:
  "verdict": [{{"index": N, "ok": true|false, "reason": "..."}} for each record]
  "off_topic_count": integer
  "suggested_reject_regex": "single regex pattern that would filter the off-topic
    records at ingest time — empty string if none needed"
  "suggested_seed_removals": [list of arch_commons_categories or source_queries
    that should be removed from the seed because they're too broad — empty if none]
  "summary": "one paragraph"
"""


def gather_records(ethn_slug: str, n: int, seed_val: int = 0):
    """Find the ethnicity's library folder(s) and pick N random records."""
    hits = []
    for meta in LIB.rglob("metadata.json"):
        parts = meta.relative_to(LIB).parts
        if len(parts) < 5 or parts[2] != ethn_slug:
            continue
        try:
            recs = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        for rec in recs:
            hits.append({
                "title": (rec.get("physical") or {}).get("title") or "",
                "desc": (rec.get("physical") or {}).get("summary") or "",
                "provider": (rec.get("location") or {}).get("current_museum") or "",
                "src": (rec.get("source") or {}).get("museum") or "",
                "cat": (rec.get("raw") or {}).get("source_category") or "",
            })
    rng = random.Random(seed_val)
    rng.shuffle(hits)
    return hits[:n]


def review(name: str, country: str, ethn_slug: str, n: int = 12) -> dict:
    records = gather_records(ethn_slug, n)
    if not records:
        return {"off_topic_count": 0, "summary": "No records in library.",
                "verdict": [], "suggested_reject_regex": "",
                "suggested_seed_removals": []}
    listed = []
    for i, r in enumerate(records):
        cat = f" cat={r['cat']}" if r["cat"] else ""
        listed.append(
            f"  {i}: [{r['src']}] {r['title'][:100]}\n"
            f"       provider={r['provider'][:60]}{cat}"
        )
    prompt = PROMPT.format(name=name, country=country, n=len(records),
                          records="\n".join(listed))
    result = ask_json(prompt)
    result["records"] = records
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="Ethnicity name (for prompt)")
    ap.add_argument("--country", required=True)
    ap.add_argument("--slug", required=True, help="Library folder slug (e.g. 'baluchi')")
    ap.add_argument("-n", type=int, default=12)
    args = ap.parse_args()
    result = review(args.name, args.country, args.slug, args.n)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
