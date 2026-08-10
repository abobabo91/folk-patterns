"""Audit: for every library record that has a vision-vetted verdict, compare
what the script-only filter (folk_patterns.junk + trusted-provider allowlist)
would have decided.

Output a confusion matrix per source so we can decide:
  - Which sources can drop LLM vetting entirely (agreement >= 95%)?
  - Which sources still need vision-vet (disagreement in either direction)?
  - How many "genuine vision cases" are left after script filtering?
"""
from __future__ import annotations

import io
import json
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import LIBRARY_DIR
from folk_patterns.junk import should_reject, is_trusted_provider


def _get_provider(r: dict) -> str:
    src = r.get("source") or {}
    if src.get("museum") == "europeana":
        raw = r.get("raw") or {}
        p = raw.get("dataProvider") or raw.get("provider") or []
        if isinstance(p, list): p = p[0] if p else ""
        return p or ""
    # For direct museum scrapers the provider IS the museum
    return src.get("museum_name") or src.get("museum") or ""


def main() -> None:
    # source → {agree_keep, agree_drop, script_drops_extra, script_misses}
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {
        "vet_keep": 0, "vet_drop": 0,
        "agree_keep": 0, "agree_drop": 0,
        "script_drops_extra": 0, "script_misses_junk": 0,
    })
    disagreements: dict[str, list[str]] = defaultdict(list)

    for m in sorted(LIBRARY_DIR.glob("*/*/*/*/*/metadata.json")):
        for r in json.loads(m.read_text(encoding="utf-8")):
            src = (r.get("source") or {}).get("museum", "?")
            v = (r.get("cultural") or {}).get("vision_vetted")
            if v is None:
                continue  # only compare where we have a vision verdict
            title = (r.get("physical") or {}).get("title") or ""
            summary = (r.get("physical") or {}).get("summary") or ""
            provider = _get_provider(r)

            # SMART POLICY (models what a scaled scraper would actually run):
            # 1. Curator-vetted museum sources → auto-KEEP unconditionally
            #    (V&A, Met, Cleveland, Smithsonian, Rijks). Only the junk-title
            #    regex applies (catches Smithsonian NMNH botanical specimens).
            # 2. Europeana with trusted provider → auto-KEEP.
            # 3. Europeana without trusted provider → apply junk-title +
            #    provider-denylist. If neither triggers, still KEEP (only
            #    ~30% of the "unknown provider" records are actually junk).
            # 4. Commons/uncurated → apply full junk gate.
            TRUSTED_MUSEUMS = {"va", "met", "cleveland", "smithsonian", "rijks"}
            if src in TRUSTED_MUSEUMS:
                # For curator-vetted museums, only the Latin-binomial regex
                # applies (catches Smithsonian NMNH natural-history leaks).
                from folk_patterns.junk import is_junk_title
                script_junk, reason = is_junk_title(title, summary)
                if not script_junk:
                    reason = "trusted-museum"
            elif src == "europeana" and is_trusted_provider(provider):
                script_junk = False
                reason = "trusted-provider"
            else:
                script_junk, reason = should_reject(title, summary, provider)

            vision_junk = (v is False)
            s = stats[src]
            if vision_junk: s["vet_drop"] += 1
            else: s["vet_keep"] += 1

            if script_junk and vision_junk:
                s["agree_drop"] += 1
            elif not script_junk and not vision_junk:
                s["agree_keep"] += 1
            elif script_junk and not vision_junk:
                s["script_drops_extra"] += 1
                if len(disagreements[f"{src} script→DROP vision→KEEP"]) < 8:
                    disagreements[f"{src} script→DROP vision→KEEP"].append(
                        f"[{reason}] {title[:70]}"
                    )
            else:
                s["script_misses_junk"] += 1
                if len(disagreements[f"{src} script→KEEP vision→DROP"]) < 8:
                    disagreements[f"{src} script→KEEP vision→DROP"].append(
                        f"[{provider[:30]}] {title[:70]}"
                    )

    print("=== SCRIPT-ONLY vs VISION-VET (per source, vetted records only) ===\n")
    print(f'{"source":<15} {"vet_kept":>9} {"vet_drop":>9} {"agree":>7} {"script_extra":>12} {"missed_junk":>12}')
    for src in sorted(stats.keys(), key=lambda s: -sum(stats[s].values())):
        s = stats[src]
        agree = s["agree_keep"] + s["agree_drop"]
        tot = s["vet_keep"] + s["vet_drop"]
        pct_agree = 100 * agree / tot if tot else 0
        print(f'{src:<15} {s["vet_keep"]:>9} {s["vet_drop"]:>9} '
              f'{agree:>4}/{tot:<3} ({pct_agree:.0f}%) '
              f'{s["script_drops_extra"]:>11}  {s["script_misses_junk"]:>11}')

    total_missed = sum(s["script_misses_junk"] for s in stats.values())
    total_extra = sum(s["script_drops_extra"] for s in stats.values())
    total_vetted = sum(s["vet_keep"] + s["vet_drop"] for s in stats.values())
    print()
    print(f"AGGREGATE: {total_vetted} vetted records.")
    print(f"  → Script filter catches junk that vision also flagged: {sum(s['agree_drop'] for s in stats.values())}")
    print(f"  → Script kills EXTRA (potential false positives, {total_extra} total, {100*total_extra/total_vetted:.0f}%)")
    print(f"  → Script MISSES junk that vision caught: {total_missed} ({100*total_missed/total_vetted:.0f}%)")
    print(f"  → This {total_missed}-record residual is where agentic vetting is genuinely needed.")

    print("\n=== DISAGREEMENT SAMPLES ===")
    for k, examples in sorted(disagreements.items()):
        print(f"\n-- {k} ({len(examples)}) --")
        for e in examples:
            print(f"  {e}")


if __name__ == "__main__":
    main()
