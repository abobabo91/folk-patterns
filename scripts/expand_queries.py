"""Auto-generate per-source scrape queries for an ethnicity using Claude CLI.

For a target ethnicity, asks Claude to propose:
  - additional TRADITIONS terms (synonyms, sub-groups, colonial-language variants)
  - cleveland_accept_tokens (narrow ethnonym + subgroup identifiers)
  - arch_commons_categories (Wikimedia signature architecture)
  - source_queries.europeana (phrases optimised for Europeana)

Merges non-destructively into the ethnicity's seed JSON entry (union with
existing lists). Idempotent: re-running only adds novel terms.

Uses `claude --print` CLI (Claude Code subscription) — never a paid API,
per user global rule.

Usage:
  # Preview (no writes)
  python scripts/expand_queries.py southeast-asia__indonesia__toraja

  # Actually write into seed JSON
  python scripts/expand_queries.py southeast-asia__indonesia__toraja --apply

  # Batch multiple
  python scripts/expand_queries.py toraja dayak hmong uyghur --apply
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = REPO_ROOT / "data" / "seed"


def load_all_seeds() -> dict[str, dict]:
    """Return {seed_filename: parsed_seed}."""
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in SEED_DIR.glob("*.json")}


def find_ethnicity(seeds: dict[str, dict], needle: str) -> tuple[str, dict, dict, dict] | None:
    """Fuzzy-locate an ethnicity by name or slug. Returns
    (seed_filename, seed, country_entry, ethnicity_entry) or None."""
    n = needle.lower().strip()
    for fname, seed in seeds.items():
        for country in seed.get("countries", []):
            for eth in country.get("ethnicities", []):
                if (
                    eth["name"].lower() == n
                    or eth["name"].lower().replace("'", "") == n.replace("'", "")
                    or (n in eth["name"].lower() and len(n) >= 4)
                    or n in fname.lower() + "__" + country["country"].lower() + "__" + eth["name"].lower()
                ):
                    return fname, seed, country, eth
    return None


PROMPT = """You are helping build a folk-culture atlas. For the ethnicity below, generate search terms that will surface their material culture in museum databases.

Ethnicity: {ethnicity}
Region: {region}
Country: {country}
Existing traditions in seed: {traditions}
Existing arch_commons_categories: {arch}
Existing cleveland_accept_tokens: {tokens}

Return ONLY a JSON object with these four keys. Include only NEW terms (not already listed above). Terms already present don't need to appear.

{{
  "additional_traditions": [
    // synonyms, sub-groups, related craft names, colonial-language variants
    // (Dutch for Indonesian material, French for Vietnamese, German for
    // Central Asian). Max 15. Only high-signal terms actual museums use as
    // tags — no invented terms.
  ],
  "cleveland_accept_tokens": [
    // Cleveland Museum uses culture-string tags like "Uyghur, Xinjiang" or
    // "Iban, Sarawak". Give 4-8 lowercase tokens that unambiguously identify
    // this ethnicity in that string. NO broad geography ("mindanao",
    // "borneo", "sulawesi") that would sweep up neighbours. Ethnonyms +
    // close subgroup names + rare place names ONLY.
  ],
  "arch_commons_categories": [
    // Wikimedia Commons category names (as they appear on commons.wikimedia.org)
    // for signature buildings, monuments, or architectural traditions of
    // this ethnicity. Empty list if the group has no distinctive built
    // heritage. Use the exact Commons category title, e.g. "Bibi-Khanym Mosque".
    //
    // For each notable building, ALSO include narrower subcategories that
    // surface interior detail (Commons often stores exterior in the parent
    // category and interior/tile/muqarnas/dome in subcategories). Typical
    // names: "Interior of X", "Muqarnas in X", "Dome of X", "Ceilings in X",
    // "Ceiling paintings in X", "Majolica tiles in X", "Mosaics in X".
    // Only include subcategories you know exist on Commons.
  ],
  "source_queries": {{
    "europeana": [
      // 3-6 phrases optimised for Europeana search (colonial European
      // museums). Include the ethnonym plus any Dutch/French/German variants
      // Europeana providers actually use. If the ethnonym is ambiguous
      // (e.g. "Chin", "Malay") wrap in quotes with a country hint like
      // '"chin" Myanmar'.
    ]
  }}
}}

Rules:
- Return ONLY the JSON object. No prose before/after. No markdown fences.
- Don't invent names. Only include terms real museums use.
- If you're not sure a category exists on Commons, leave it out.
- Don't repeat any existing term.
"""


def ask_claude(prompt: str) -> str:
    """Shell out to `claude --print`. Blocks until CLI returns.

    On Windows the `claude` command is a .cmd shim which subprocess.run
    can't find without shell=True (or an explicit .cmd suffix). Use
    shell=True so both platforms resolve it via PATH.
    """
    cmd = 'claude --print --model claude-opus-5'
    proc = subprocess.run(
        cmd, shell=True,
        input=prompt.encode("utf-8"),
        capture_output=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.decode('utf-8', errors='replace')}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


def parse_json_reply(text: str) -> dict:
    """Salvage JSON from a reply. Handles ```json fences and stray prose."""
    t = text.strip()
    # Strip fences
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip("` \n")
    # Try direct
    try:
        return json.loads(t)
    except Exception:
        pass
    # Find first {...} block
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        return json.loads(t[i:j + 1])
    raise ValueError(f"couldn't parse JSON from reply: {text[:200]}")


def _merge_list(existing: list | None, additions: list) -> tuple[list, list]:
    """Return (new_full_list, actually_added). Case-insensitive dedup for
    strings; preserve ordering (existing first, then new)."""
    existing = list(existing or [])
    seen_lower = {str(x).lower() for x in existing if isinstance(x, str)}
    added: list = []
    for x in additions:
        if isinstance(x, str) and x.lower() not in seen_lower:
            existing.append(x)
            added.append(x)
            seen_lower.add(x.lower())
    return existing, added


def expand_one(target_ref: str, apply: bool) -> None:
    seeds = load_all_seeds()
    match = find_ethnicity(seeds, target_ref)
    if not match:
        print(f"  ! no ethnicity matches {target_ref!r}", flush=True)
        return
    fname, seed, country, eth = match
    print(f"\n=== {eth['name']} ({country['country']}, {seed['region']}) — from {fname} ===")
    prompt = PROMPT.format(
        ethnicity=eth["name"],
        region=seed["region"],
        country=country["country"],
        traditions=json.dumps(eth.get("traditions") or [], ensure_ascii=False),
        arch=json.dumps(eth.get("arch_commons_categories") or [], ensure_ascii=False),
        tokens=json.dumps(eth.get("cleveland_accept_tokens") or [], ensure_ascii=False),
    )
    print("  asking Claude…", flush=True)
    reply = ask_claude(prompt)
    data = parse_json_reply(reply)

    changes: dict[str, list] = {}
    # traditions
    new_trads, added = _merge_list(eth.get("traditions"), data.get("additional_traditions") or [])
    if added:
        changes["traditions"] = added
        if apply:
            eth["traditions"] = new_trads
    # cleveland accept tokens
    new_tokens, added = _merge_list(eth.get("cleveland_accept_tokens"), data.get("cleveland_accept_tokens") or [])
    if added:
        changes["cleveland_accept_tokens"] = added
        if apply:
            eth["cleveland_accept_tokens"] = new_tokens
    # arch commons categories
    new_arch, added = _merge_list(eth.get("arch_commons_categories"), data.get("arch_commons_categories") or [])
    if added:
        changes["arch_commons_categories"] = added
        if apply:
            eth["arch_commons_categories"] = new_arch
    # source_queries.europeana
    eu_new = (data.get("source_queries") or {}).get("europeana") or []
    if eu_new:
        current_eu = ((eth.get("source_queries") or {}).get("europeana")) or []
        merged_eu, added_eu = _merge_list(current_eu, eu_new)
        if added_eu:
            changes["source_queries.europeana"] = added_eu
            if apply:
                eth.setdefault("source_queries", {})["europeana"] = merged_eu

    if not changes:
        print("  no new terms proposed")
        return

    for field, added in changes.items():
        print(f"  + {field:32}: {added}")

    if apply:
        (SEED_DIR / fname).write_text(json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  written to data/seed/{fname}")
        # Auto-invalidate raw scraper caches for this ethnicity so a
        # subsequent re-scrape actually uses the new queries.
        removed = _clear_raw_caches(eth["name"], country["country"])
        if removed:
            print(f"  cleared {removed} stale raw caches")
    else:
        print("  (preview only — pass --apply to write)")


def _clear_raw_caches(ethnicity: str, country: str) -> int:
    """Delete raw/<source>/*<ethnicity>* caches so next scrape hits the API
    with the fresh queries. Idempotent — silently does nothing if no cache."""
    from pathlib import Path as _P
    from slugify import slugify as _slug
    raw = _P(__file__).resolve().parents[1] / "data" / "raw"
    if not raw.exists():
        return 0
    ethn_slug = _slug(ethnicity)
    cty_slug = _slug(country)
    removed = 0
    for src_dir in raw.iterdir():
        if not src_dir.is_dir():
            continue
        for p in src_dir.glob("*.json"):
            n = p.stem.lower()
            if ethn_slug in n and (not cty_slug or cty_slug in n):
                p.unlink()
                removed += 1
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="+", help="Ethnicity names, slugs, or seed keys (fuzzy match)")
    ap.add_argument("--apply", action="store_true", help="Write changes to seed JSON (default: preview)")
    args = ap.parse_args()

    for t in args.targets:
        try:
            expand_one(t, apply=args.apply)
        except Exception as e:
            print(f"  ! failed for {t!r}: {e}", flush=True)


if __name__ == "__main__":
    main()
