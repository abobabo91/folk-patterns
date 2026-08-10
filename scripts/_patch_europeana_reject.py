"""Append an LLM-suggested reject pattern to europeana._AMBIGUOUS_ETHNONYM_REJECT.

Called from add_culture.py after the ambiguity probe or sample review returns
a suggested regex. Shows the diff and confirms before writing (unless --yes).

Idempotent: if the pattern is already there for the ethnicity, no-op.
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

EUR_PATH = Path(__file__).resolve().parents[1] / "src" / "folk_patterns" / "museums" / "europeana.py"


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-").replace("'", "")


def patch(ethnicity: str, pattern: str, yes: bool = False) -> bool:
    if not pattern.strip():
        return False
    slug = _slug(ethnicity)
    src = EUR_PATH.read_text(encoding="utf-8")

    # Find the _AMBIGUOUS_ETHNONYM_REJECT dict literal.
    m = re.search(r"_AMBIGUOUS_ETHNONYM_REJECT\s*:\s*dict\[str,\s*list\[str\]\]\s*=\s*\{", src)
    if not m:
        print(f"! could not locate _AMBIGUOUS_ETHNONYM_REJECT in {EUR_PATH}", file=sys.stderr)
        return False

    # Escape backslashes for python source
    pat_escaped = pattern.replace("\\", "\\\\").replace('"', '\\"')

    # If this ethnicity already has an entry, append to its list.
    key_pat = re.compile(rf'"{re.escape(slug)}"\s*:\s*\[')
    km = key_pat.search(src, m.end())
    if km:
        # Idempotent check: if the pattern text is already in that block, skip
        # (find end of this list — first ] after km.end())
        list_end = src.index("]", km.end())
        existing = src[km.end():list_end]
        if pat_escaped in existing:
            print(f"  [skip] pattern already present for {slug!r}")
            return False
        insert_at = list_end
        new_src = (
            src[:insert_at]
            + f',\n        r"{pat_escaped}"'
            + src[insert_at:]
        )
    else:
        # New ethnicity key — insert after the opening brace
        insert_at = m.end()
        new_src = (
            src[:insert_at]
            + f'\n    "{slug}": [\n        r"{pat_escaped}",\n    ],'
            + src[insert_at:]
        )

    print(f"\n--- proposed patch to {EUR_PATH.name} ---")
    print(f"  ethnicity: {slug}")
    print(f"  pattern:   r\"{pat_escaped}\"")
    if not yes:
        print(f"\nApply? [y/N]: ", end="", flush=True)
        if input().strip().lower() != "y":
            print("skipped.")
            return False

    EUR_PATH.write_text(new_src, encoding="utf-8")
    print(f"  patched {EUR_PATH}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ethnicity", required=True)
    ap.add_argument("--pattern", required=True)
    ap.add_argument("--yes", "-y", action="store_true")
    args = ap.parse_args()
    patch(args.ethnicity, args.pattern, args.yes)


if __name__ == "__main__":
    main()
