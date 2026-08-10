"""Fetch full Europeana Record API metadata for every Europeana object in the
library, and backfill materials / techniques / dimensions / description into
the canonical record.

The search-response items we saved during scraping lack these fields, so
Europeana object detail pages currently look sparser than V&A ones. This
script closes that gap.

Idempotent: skips records whose `physical.medium_raw` (a proxy for whether
enrichment already ran) is already populated, unless --force.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import LIBRARY_DIR, RateLimitedClient

VAULT = Path("C:/Users/abele/Desktop/github/tools/vault/vault.toml")


def _get_key() -> str:
    return tomllib.load(open(VAULT, "rb"))["apis"]["europeana"]["key"]


def _flatten_lang_aware(v) -> list[str]:
    """Europeana returns fields as dicts like {"en": ["..."], "nl": ["..."]}.
    Flatten to a single list preferring English."""
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    if isinstance(v, dict):
        out: list[str] = []
        for lang in ("en", "def", "nl", "sv", "de", "fr"):
            if lang in v and isinstance(v[lang], list):
                out += [x for x in v[lang] if isinstance(x, str)]
        for lang, val in v.items():
            if lang in ("en", "def", "nl", "sv", "de", "fr"):
                continue
            if isinstance(val, list):
                out += [x for x in val if isinstance(x, str)]
        return out
    if isinstance(v, str):
        return [v]
    return []


def enrich_one(client: RateLimitedClient, record_id: str, key: str) -> dict | None:
    """Return {materials, techniques, description, dimensions, date_text}
    or None if the record can't be fetched."""
    url = f"https://api.europeana.eu/record/v2{record_id}.json"
    try:
        j = client.get_json(url, params={"wskey": key})
    except Exception:
        return None
    obj = j.get("object") or {}
    proxies = obj.get("proxies") or []
    if not proxies:
        return None
    p = proxies[0]

    out: dict = {"materials": [], "techniques": [], "description": None, "date_text": None, "dimensions_note": None}

    # Description — dcDescription is a langAware dict of lists.
    descs = _flatten_lang_aware(p.get("dcDescription"))
    if descs:
        out["description"] = descs[0][:1500]

    # Material — dcTermsMedium OR dctermsMedium OR dcTermsExtent for dimensions.
    for k in ("dctermsMedium", "dcTermsMedium"):
        for v in _flatten_lang_aware(p.get(k)):
            out["materials"].append(v)

    # Technique — dcSubject sometimes, else dcType (already have) — skip.
    # Extent — dctermsExtent has physical dimensions.
    for k in ("dctermsExtent", "dcTermsExtent"):
        for v in _flatten_lang_aware(p.get(k)):
            if not out["dimensions_note"]:
                out["dimensions_note"] = v
            else:
                out["dimensions_note"] += " · " + v

    # Date
    for k in ("dctermsCreated", "dcDate"):
        for v in _flatten_lang_aware(p.get(k)):
            if not out["date_text"]:
                out["date_text"] = v
                break
        if out["date_text"]:
            break

    return out


def process_record(client, key, meta_path, r):
    """Enrich a single record in-place. Returns True if changed."""
    src = r.get("source") or {}
    if src.get("museum") != "europeana":
        return False
    if (r.get("physical") or {}).get("medium_raw"):
        return False  # already enriched
    # The Europeana record ID is inside raw.id (e.g. "/2048221/foo_bar")
    raw = r.get("raw") or {}
    rec_id = raw.get("id") or raw.get("guid")
    if not rec_id:
        return False
    fresh = enrich_one(client, rec_id, key)
    if not fresh:
        return False
    phys = r.setdefault("physical", {})
    if fresh["description"]:
        phys["summary"] = fresh["description"]
    if fresh["materials"]:
        phys["materials"] = fresh["materials"][:6]
        phys["medium_raw"] = "; ".join(fresh["materials"][:3])
    if fresh["dimensions_note"]:
        phys["dimensions_note"] = fresh["dimensions_note"]
    if fresh["date_text"] and not phys.get("date_text"):
        phys["date_text"] = fresh["date_text"]
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="Stop after N records (0 = all)")
    args = ap.parse_args()

    key = _get_key()
    client = RateLimitedClient(min_interval_s=0.4)
    total = 0
    enriched = 0
    # Batch records per file so we write once per file.
    for meta_path in sorted(LIBRARY_DIR.glob("*/*/*/*/*/metadata.json")):
        recs = json.loads(meta_path.read_text(encoding="utf-8"))
        # Filter to Europeana records that need enrichment.
        targets = [r for r in recs if (r.get("source") or {}).get("museum") == "europeana"
                   and not (r.get("physical") or {}).get("medium_raw")]
        if not targets:
            continue
        dirty = False
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(process_record, client, key, meta_path, r): r for r in targets}
            for f in as_completed(futs):
                total += 1
                try:
                    if f.result():
                        enriched += 1
                        dirty = True
                except Exception as e:
                    print(f"  ! {e}", flush=True)
                if args.limit and enriched >= args.limit:
                    break
        if dirty:
            meta_path.write_text(json.dumps(recs, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[enrich] {meta_path.relative_to(LIBRARY_DIR)}  targets={len(targets)}", flush=True)
        if args.limit and enriched >= args.limit:
            break
    client.close()
    print(f"\nEnriched {enriched} / {total} Europeana records")


if __name__ == "__main__":
    main()
