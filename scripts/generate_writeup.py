"""Generate a folk-culture writeup for an ethnicity via Claude CLI.

Uses the format established by the hand-written Central Asian writeups —
frontmatter + Overview + Material culture (with subsections) + Music/dance +
Festivals + Foodways + Oral tradition + Language & religion + Sources.

One LLM call per culture. Skips ethnicities that already have a writeup.

Usage:
  python scripts/generate_writeup.py Persian Turkish Berber Egyptian ...
  python scripts/generate_writeup.py --all-missing            # backfill everything without a writeup
  python scripts/generate_writeup.py --all-missing --dry-run  # just list what would be generated
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import subprocess
import sys
from pathlib import Path

from slugify import slugify

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = REPO_ROOT / "data" / "seed"
CONTENT_DIR = REPO_ROOT / "content"

# Load one exemplar writeup to give Claude the exact format to mirror.
_EXEMPLAR_PATH = CONTENT_DIR / "central-asia" / "uzbekistan__uzbek.md"


def load_exemplar() -> str:
    if not _EXEMPLAR_PATH.exists():
        return ""
    return _EXEMPLAR_PATH.read_text(encoding="utf-8")[:8000]  # cap so prompt isn't huge


def find_ethnicity(name: str) -> tuple[Path, dict, dict, dict] | None:
    """Return (seed_path, seed, country, ethnicity) matching name (fuzzy)."""
    n = name.lower().strip()
    for p in SEED_DIR.glob("*.json"):
        seed = json.loads(p.read_text(encoding="utf-8"))
        for c in seed.get("countries", []):
            for e in c.get("ethnicities", []):
                if (
                    e["name"].lower() == n
                    or slugify(e["name"]) == slugify(name)
                    or (n in e["name"].lower() and len(n) >= 4 and "(" not in e["name"])
                ):
                    return p, seed, c, e
    return None


def writeup_path(seed: dict, country: dict, ethnicity: dict) -> Path:
    region_slug = slugify(seed["region"])
    return CONTENT_DIR / region_slug / f"{slugify(country['country'])}__{slugify(ethnicity['name'])}.md"


PROMPT_TEMPLATE = """You are writing a folk-culture atlas entry. The style + section structure MUST mirror the exemplar below exactly. Rich, dense, encyclopedic prose. Foreign vocabulary in *italics*. No bullet lists except in the material-culture subsections. Do not fabricate — if a category doesn't apply to this group, still write a short paragraph explaining why (e.g. "Nomadic Fulani have no signature architecture …").

EXEMPLAR (Uzbek, for STYLE ONLY — do not copy content):
---
{exemplar}
---

Now write the corresponding entry for:
- Ethnicity: {ethnicity_name}
- Country: {country_name}
- Region: {region_name}
- Homeland: {homeland_place}
- Named traditions in the seed: {traditions}

REQUIRED SECTIONS (## headings), in this order:
1. Overview — one long paragraph placing the group linguistically, historically, geographically. Population estimate. Signature material contributions.
2. Material culture — with these ### subheadings (skip any that genuinely don't apply, but include most):
   - Textile & pattern traditions
   - Clothing & dress
   - Architecture
   - Ceramics, metalwork & everyday objects
   - Jewelry & body adornment
3. Music & performance
4. Dance & theatre
5. Festivals & rituals
6. Foodways
7. Oral tradition & literature
8. Language & religion
9. Sources & further reading — short list of Wikipedia + UNESCO ICH + any specialist scholarship

Frontmatter (must be first block):
---
title: "{ethnicity_name}"
subtitle: "{country_name}"
region: "{region_pretty}"
tags: [ethnography, {region_tag}]
---

RULES:
- 1500-2500 words total
- Italicize local terms on first mention: *khan-atlas*, *nkisi*, *aso oke*
- Cite UNESCO ICH inscriptions by name when relevant
- Return the markdown ONLY, no preamble or explanation
- If uncertain about a specific claim, use hedged phrasing ("commonly", "in some communities") rather than inventing details
"""


def ask_claude(prompt: str) -> str:
    proc = subprocess.run(
        "claude --print --model claude-opus-5", shell=True,
        input=prompt.encode("utf-8"),
        capture_output=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.decode('utf-8', errors='replace')}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


def region_pretty(slug: str) -> str:
    return slug.replace("-", " ").title().replace("And", "&")


def region_tag(slug: str) -> str:
    return slug


def generate_one(name: str, exemplar: str, dry_run: bool = False, overwrite: bool = False) -> bool:
    match = find_ethnicity(name)
    if not match:
        print(f"  ! no seed entry for {name!r}")
        return False
    seed_path, seed, country, eth = match
    dst = writeup_path(seed, country, eth)
    if dst.exists() and not overwrite:
        print(f"  skip {eth['name']} — writeup already exists")
        return False
    if dry_run:
        print(f"  would generate: {dst.relative_to(REPO_ROOT)}")
        return True

    prompt = PROMPT_TEMPLATE.format(
        exemplar=exemplar,
        ethnicity_name=eth["name"],
        country_name=country["country"],
        region_name=seed["region"],
        region_pretty=region_pretty(seed["region"]),
        region_tag=slugify(seed["region"]),
        homeland_place=eth.get("homeland_place") or "",
        traditions=", ".join(eth.get("traditions") or [])[:800],
    )
    print(f"  ▸ generating {eth['name']} ({country['country']}) …", flush=True)
    reply = ask_claude(prompt)
    # Strip code fences if Claude wrapped it
    text = reply.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("markdown"):
            text = text[8:]
        text = text.strip("` \n")
    # Sanity check: must start with frontmatter
    if not text.lstrip().startswith("---"):
        print(f"  ! reply doesn't look like markdown frontmatter, first 100 chars: {text[:100]!r}")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    print(f"    wrote {dst.relative_to(REPO_ROOT)} ({len(text)} chars)")
    return True


def all_missing() -> list[str]:
    """List every ethnicity across all seeds that has no writeup."""
    missing: list[str] = []
    for p in SEED_DIR.glob("*.json"):
        seed = json.loads(p.read_text(encoding="utf-8"))
        for c in seed["countries"]:
            for e in c["ethnicities"]:
                dst = writeup_path(seed, c, e)
                if not dst.exists():
                    missing.append(e["name"])
    return missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="Ethnicity names (or --all-missing)")
    ap.add_argument("--all-missing", action="store_true", help="Generate writeups for every ethnicity that lacks one")
    ap.add_argument("--dry-run", action="store_true", help="Just list what would be generated")
    ap.add_argument("--overwrite", action="store_true", help="Regenerate even if writeup already exists")
    args = ap.parse_args()

    targets = args.names
    if args.all_missing:
        missing = all_missing()
        print(f"{len(missing)} ethnicities missing writeups: {missing}")
        targets = missing + [t for t in targets if t not in missing]

    if not targets:
        print("nothing to do")
        return

    exemplar = load_exemplar()
    if not exemplar:
        print("warning: exemplar not found; style may drift")

    ok = 0
    for name in targets:
        if generate_one(name, exemplar, dry_run=args.dry_run, overwrite=args.overwrite):
            ok += 1

    print(f"\n{ok}/{len(targets)} writeups generated")


if __name__ == "__main__":
    main()
