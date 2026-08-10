"""Claude-CLI enrichment: given a seed of (country, ethnicity, starter traditions),
ask Claude to propose additional named traditions worth searching for.

Per user's global CLAUDE.md rule (`Never use a paid LLM API ... without explicitly
asking me first`), we shell out to `claude --print` — the CLI subscription is
already paid for.

Call pattern matches `tinder-driver/pipelines/common.py`.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

MODEL = "claude-opus-5"


def run_claude(prompt: str, timeout: int = 300) -> str:
    result = subprocess.run(
        f"claude --print --dangerously-skip-permissions "
        f"--no-session-persistence --model {MODEL}",
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        shell=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude --print exit {result.returncode}: {result.stderr[:500]}")
    return result.stdout.strip()


PROMPT_TEMPLATE = """You are helping build a per-ethnicity folk-art pattern reference library. For the ethnic group below, list every named traditional craft, textile, embroidery, tile-work, or ornament tradition that has its own distinctive vocabulary and would be searchable in a museum catalog (Met, V&A, Rijksmuseum, Cooper Hewitt).

Ethnic group: {ethnicity}
Country: {country}
Region: {region}
Seed traditions already known: {seed_traditions}

Return a JSON array (no prose, no code fences, just the JSON array) of tradition objects. Each object has:
- "name": the specific vernacular name (e.g. "suzani", "shyrdak", "adire eleko"). Use romanized spelling most common in museum catalogs.
- "category": one of "textile", "embroidery", "carpet", "felt", "tile", "ornament", "ceramic", "jewelry", "clothing", "other"
- "notes": one short sentence explaining what it is and where it appears
- "search_terms": array of 2-4 alternate spellings or related keywords to try in museum searches

Rules:
- Include the seed traditions in your output (do not duplicate but do include them).
- Only include real, documented traditions. Do not invent.
- If you're not sure a tradition is real for this specific ethnic group, omit it.
- Prefer specific named patterns over generic categories (e.g. "Tekke gul" over "carpet motif").
- Aim for 10-30 entries total.

Respond with ONLY the JSON array."""


def _strip_json_fence(s: str) -> str:
    # Handle if Claude wraps in ```json ... ``` despite instructions.
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", s, re.DOTALL)
    if m:
        return m.group(1)
    # Sometimes model adds a preface paragraph, then the array.
    m = re.search(r"(\[\s*\{.*\}\s*\])", s, re.DOTALL)
    if m:
        return m.group(1)
    return s


def enrich_ethnicity(country: str, ethnicity: str, region: str, seed_traditions: list[str]) -> list[dict]:
    prompt = PROMPT_TEMPLATE.format(
        country=country,
        ethnicity=ethnicity,
        region=region,
        seed_traditions=json.dumps(seed_traditions, ensure_ascii=False),
    )
    raw = run_claude(prompt)
    payload = _strip_json_fence(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude returned non-JSON:\n{raw[:1000]}") from e
    if not isinstance(data, list):
        raise RuntimeError(f"Expected JSON array, got {type(data).__name__}")
    return data
