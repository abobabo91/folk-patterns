"""Auto-classify unclassified records via Claude CLI.

Scans the built shards for records in the `unclassified` bucket, groups
them by (tradition, first-3-title-tokens), asks Claude which art_form
each group belongs to, and writes the mappings to a JSON override file
that build_index.py consults to reclassify without a re-scrape.

The override is per-record (by id) so precision is high — no false
positives from broad keyword additions. Idempotent: re-running only
adds mappings for records still stuck in unclassified.

Uses `claude --print` CLI (Claude Code subscription) — no paid API.

Usage:
  python scripts/expand_classifier.py           # preview
  python scripts/expand_classifier.py --apply   # write to data/classifier_overrides.json
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
OVERRIDE_PATH = DATA_DIR / "classifier_overrides.json"

VALID_ART_FORMS = {
    "textile", "garment", "ceramic", "architectural", "jewelry",
    "metalwork", "painting-mss", "sculpture", "wallpaper",
    "household", "photo",
    "reject",   # for records that are actually junk and should be dropped
}


def collect_unclassified() -> list[dict]:
    """Return list of {id, ethnicity, source, tradition, title, description}
    for every record currently in an 'unclassified' shard bucket."""
    out: list[dict] = []
    seen: set[str] = set()
    for p in glob.glob(str(DATA_DIR / "ethnicities" / "*.json")):
        sh = json.loads(Path(p).read_text(encoding="utf-8"))
        for it in (sh.get("art_form_buckets") or {}).get("unclassified", []):
            rid = it.get("id")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            out.append({
                "id": rid,
                "source": it.get("source"),
                "ethnicity": sh["ethnicity"],
                "country": sh["country"],
                "tradition": it.get("tradition") or "",
                "title": it.get("title") or "",
                "place": it.get("place") or "",
            })
    return out


PROMPT = """You are helping a folk-culture atlas assign an `art_form` category to museum records currently stuck in an "unclassified" bucket.

For each record below, choose ONE of:
- textile: cloths, hangings, embroideries, weavings, felt (not clothing)
- garment: clothing, jackets, robes, hats, shoes, jewelry-adjacent adornment
- ceramic: pottery, plates, bowls, jars, glazed vessels
- architectural: buildings, monuments, temple photos, mosque photos
- jewelry: earrings, necklaces, pendants, ornamental body pieces (metal + stone)
- metalwork: swords, knives, hilts, blades, decorative metalwork
- painting-mss: manuscripts, folios, paintings, calligraphy
- sculpture: statues, figurines, carved wood/stone figures, masks, dolls
- wallpaper: printed textile designs, tile designs, wall coverings
- household: fans, brooms, baskets, tools, utensils, containers, cooking
- photo: documentary photographs of people, ceremonies, daily life
- reject: this is NOT a folk-culture object — it's a natural-history specimen (bird / plant / animal), a coin, a map, a wrong-country record, or otherwise junk. Use sparingly.

Records:
{records}

Return ONLY a JSON object mapping record id to art_form choice, e.g.:
{{
  "commons_arch-abc123": "architectural",
  "europeana-xyz789": "sculpture",
  "smithsonian-def456": "reject"
}}

No prose, no markdown fences. If you can't decide, omit the record from the output.
"""


# Haiku, not opus. Benchmarked 2026-08-10 on a 30-record batch against the
# opus incumbent (which agreed with itself 100%, so differences here are real):
#
#   opus-4-7   27.7s  $0.3049  classified 24/30
#   opus-5     18.0s  $0.2945  classified 30/30
#   haiku-4.5  14.7s  $0.0472  classified 30/30   <- 1.9x faster, 6.5x cheaper
#
# Haiku is not merely cheaper, it is more accurate on this task. The 6 records
# opus refused to classify are junk titled "crap" / "generalkatalog" /
# "katalogkort", which haiku correctly rejects instead of leaving stuck in
# unclassified forever. On the one shared record where they differ -- "Model van
# een Surinaams-Creoolse hoofddoek" -- opus said reject and haiku said garment;
# haiku is right. Bucketing a record into one of 12 named categories from a
# one-line title is pattern-matching, not judgement.
MODEL = "claude-haiku-4-5-20251001"


def ask_claude(prompt: str) -> str:
    proc = subprocess.run(
        f"claude --print --model {MODEL}", shell=True,
        input=prompt.encode("utf-8"),
        capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.decode('utf-8', errors='replace')}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


def parse_json_reply(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip("` \n")
    try:
        return json.loads(t)
    except Exception:
        pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        return json.loads(t[i:j + 1])
    raise ValueError(f"couldn't parse JSON: {text[:200]}")


def format_records_for_prompt(records: list[dict]) -> str:
    lines: list[str] = []
    for r in records:
        # Compact one-line summary — id first for Claude to reference back
        lines.append(
            f"- id={r['id']} | {r['ethnicity']} | tradition={r['tradition']!r} | "
            f"title={r['title'][:80]!r}"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write mappings to data/classifier_overrides.json")
    ap.add_argument("--batch-size", type=int, default=30, help="How many records per Claude call")
    args = ap.parse_args()

    records = collect_unclassified()
    print(f"{len(records)} unclassified records in current shards")

    # Skip records we've already classified in a previous run
    existing: dict = {}
    if OVERRIDE_PATH.exists():
        existing = json.loads(OVERRIDE_PATH.read_text(encoding="utf-8"))
        records = [r for r in records if r["id"] not in existing]
        print(f"  ({len(existing)} already in overrides, {len(records)} new)")

    if not records:
        print("nothing to do")
        return

    all_mappings: dict[str, str] = {}
    for i in range(0, len(records), args.batch_size):
        batch = records[i:i + args.batch_size]
        print(f"\n[{i+1}-{i+len(batch)}/{len(records)}] asking Claude…")
        prompt = PROMPT.format(records=format_records_for_prompt(batch))
        try:
            reply = ask_claude(prompt)
            mapped = parse_json_reply(reply)
        except Exception as e:
            print(f"  ! batch failed: {e}")
            continue
        # Validate
        for rid, af in mapped.items():
            if af not in VALID_ART_FORMS:
                print(f"  ! invalid art_form {af!r} for {rid}")
                continue
            all_mappings[rid] = af
        print(f"  got {len(mapped)} mappings")

    # Report
    from collections import Counter
    counts = Counter(all_mappings.values())
    print(f"\n=== proposed mappings ===")
    for af, n in counts.most_common():
        print(f"  {n:3}  → {af}")

    if not args.apply:
        print("\n(preview only — pass --apply to write)")
        return

    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = {**existing, **all_mappings}
    OVERRIDE_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {len(merged)} total overrides to {OVERRIDE_PATH.name}")
    print("run `python scripts/build_index.py` to apply")


if __name__ == "__main__":
    main()
