"""Claude-CLI-based writeup generator for full-scope ethnographic profiles.

Produces a structured markdown per (country, ethnicity) covering both material
culture (traditionally the museum-object focus of this project) and intangible
heritage (music, dance, festivals, foodways, oral tradition) — a proper
néprajzi/ethnographic profile rather than a pattern-only writeup.

Per CLAUDE.md rule (`Never use a paid LLM API without explicitly asking me first`),
shell out to `claude --print`. Same subprocess pattern as
`tinder-driver/pipelines/common.py`.

The output is markdown with YAML frontmatter — plays well with Astro Content
Collections but also renders as plain markdown anywhere.
"""
from __future__ import annotations

import json
import subprocess

MODEL = "claude-opus-5"


def run_claude(prompt: str, timeout: int = 900) -> str:
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


PROMPT_TEMPLATE = """You are drafting a rigorous ethnographic profile of one ethnic group for a
research atlas of world folk culture (a "néprajzi" summary, in the Hungarian
sense — covering both material and intangible culture). It appears alongside
photographs of the group's museum-held objects and links to external sources.

Ethnic group: {ethnicity}
Country / region: {country} / {region}
Seed textile / pattern traditions we already index: {seed_traditions}

Write a markdown document with the following exact structure. Use plain
markdown (no HTML, no code fences around the whole doc). Total length ~1200–1800
words. Prefer specificity to generality — real named traditions, real motif
names, real instrument names, real dish names, real historical periods.

---
title: "{ethnicity}"
subtitle: "{country}"
region: "{region}"
tags: [ethnography, {region_slug}]
---

## Overview

<One paragraph: who the {ethnicity} are, where they live (be specific — river
valley, city, oasis, mountain range), roughly how many, language family, and
why they matter in folk-culture terms. 100–150 words.>

## Material culture

### Textile & pattern traditions

<This is the section that ties to our pattern gallery. For each real,
documented pattern-bearing textile tradition, write:

**<Vernacular Name>** — <1–2 sentences: what it is, materials/technique, what
distinguishes it from neighboring cultures' equivalents. Italicize vernacular
names on first mention.>

Include the seed traditions listed above when they are legitimately
distinctive to this group. Add other well-documented textiles you're confident
about. 4–7 entries.>

**Motif vocabulary.** <Brief motif list, comma-separated with brief glosses.
5–10 named motifs.>

### Clothing & dress

<Both everyday and ceremonial dress. Distinguish men's and women's if relevant.
Name specific garment types with vernacular terms. Mention head coverings,
belts, footwear, and any ceremonial dress distinct from daily wear. 100–200 words.>

### Architecture

<Vernacular built environment: house form, materials, roof type, decoration.
Include named building types (yurt/aq oy, rumah gadang, tongkonan, etc.),
distinctive structural or ornamental features, and if relevant, urban
traditions (courtyard house, workshop). 100–200 words.>

### Ceramics, metalwork & everyday objects

<Ceramics, metalwork, wooden objects, tools, and household goods that carry
cultural identity. Named forms (Rishtan blue-and-white, Turkmen silver amulet,
etc.). 80–150 words.>

### Jewelry & body adornment

<Jewelry types, materials, ritual functions. Include tattoos, henna, hair
practices if documented. Named types like tumar, gulyaka, saukele where they
exist. 80–150 words.>

## Music & performance

<Instruments (name them — dutar, gopichand, sape, kim), song genres (dastan,
lakon, kroncong), performance contexts (weddings, funerals, court, tea house).
Reference specific traditions like Central Asian shashmaqam or Javanese
gamelan by name. 150–250 words.>

## Dance & theatre

<Named dances and dramatic traditions (shadow puppet, mask dance, court dance).
Include ceremonial vs. entertainment distinctions. 100–200 words.>

## Festivals & rituals

<Annual festival calendar (Nowruz, harvest, Ramadan-adjacent, seasonal) plus
life-cycle rites (birth, coming-of-age, wedding, funeral). Name specific
festivals with dates or seasons where possible. 150–250 words.>

## Foodways

<Staple grains, cooking methods, signature dishes (with vernacular names —
plov, gudeg, laksa, khao soi), ceremonial food, tea/coffee traditions, dietary
rules (halal, vegetarian temple food). 150–250 words.>

## Oral tradition & literature

<Folktales, epic poetry, proverbs, riddles, storytelling contexts. Name the
epic if there is one (Alpamysh, Ramayana wayang tradition, Panji cycle).
Include contemporary literary revivals or preservation efforts. 100–200 words.>

## Language & religion

<Language family, dialects, historical script(s), current religious landscape
(sect, syncretism), notable spiritual practices tied to folk culture. 100–150 words.>

## Sources & further reading

- <3–4 real books with author, title, publisher, year>
- <Any well-known scholar or documentation project (e.g. Alexander Djumaev on
  Central Asian music; Nancy Van Deusen; specific Southeast Asian textile
  scholars)>
- <Wikipedia article URL for the group when it exists — form the URL as
  https://en.wikipedia.org/wiki/{{ethnicity_slug}}. Use your best guess for
  the slug.>
- <UNESCO Intangible Cultural Heritage list URL if this group has entries —
  https://ich.unesco.org/en/state/{{country-code}} where you know it>
- <Smithsonian Folkways search URL when music tradition is prominent —
  https://folkways.si.edu/search?query={{ethnicity+or+country}}>
- <Relevant museum online-collection URLs (V&A, Met, Rijksmuseum)>

Rules:
- Do NOT invent traditions, motifs, dishes, instruments, or references. If
  you're not confident, omit that entry. Better a short accurate writeup than
  a padded one.
- If a section genuinely has little documented material for a small group,
  write ONE sentence explaining that rather than filler.
- Vernacular names in *italics* on first mention.
- **Bold** names of specific traditions when listing them.
- Do NOT use hedging phrases like "may be" or "some scholars believe" unless
  it's a genuine scholarly debate worth noting.
- Do NOT reuse phrases like "rich cultural heritage" or "traditional craft" —
  be specific.
- Output nothing except the markdown document — no preamble, no code fence."""


def make_prompt(country: str, ethnicity: str, region: str, seed_traditions: list[str]) -> str:
    from slugify import slugify
    return PROMPT_TEMPLATE.format(
        country=country,
        ethnicity=ethnicity,
        region=region.replace("-", " ").title(),
        region_slug=slugify(region),
        seed_traditions=", ".join(seed_traditions) if seed_traditions else "(none provided)",
    )


GROUNDING_PREAMBLE = """You will be given source material below. Use it as your primary grounding
— prefer facts, terms, and traditions that appear in these sources over your
own recollection. Your own knowledge is welcome IN ADDITION to (never
contradicting) the sources. Cite the Wikipedia URL and any UNESCO ICH
identifier(s) in the "Sources & further reading" list at the end.

============ SOURCE 1: Wikipedia article "{wiki_title}" ============
URL: {wiki_url}

{wiki_text}

============ SOURCE 2: UNESCO Intangible Cultural Heritage inscriptions ============
The following ICH elements have {country} listed as a country of origin. Each
has a canonical page at https://ich.unesco.org/en/{{code}}. Reference them by
name in the relevant sections (Music, Dance, Foodways, Festivals, etc.) when
they apply to this ethnic group specifically. In "Sources & further reading",
include the code and URL for any you reference.

{ich_block}

============ END SOURCES ============

Now write the writeup according to the template below.

"""


def make_grounded_prompt(country: str, ethnicity: str, region: str, seed_traditions: list[str],
                         wiki: dict | None, ich: list[dict] | None) -> str:
    """Prepend Wikipedia + UNESCO ICH grounding context to the base prompt.

    `wiki` shape: {title, url, intro, full_text, ...} (from media.wiki_fetch_article)
    `ich`  shape: [{code, title, unesco_url, description}] (from media.unesco_ich_for_country)"""
    base = make_prompt(country, ethnicity, region, seed_traditions)
    if not wiki and not ich:
        return base
    wiki_text = ""
    wiki_title = wiki_url = "(none)"
    if wiki:
        wiki_title = wiki.get("title") or "(none)"
        wiki_url = wiki.get("url") or "(none)"
        wiki_text = wiki.get("full_text") or wiki.get("intro") or ""
    if ich:
        ich_lines = [f'- {e["code"]}: "{e["title"]}"' + (f' — {e["description"]}' if e.get("description") else "")
                     for e in ich]
        ich_block = "\n".join(ich_lines) if ich_lines else "(none)"
    else:
        ich_block = "(none — this country has no UNESCO ICH inscriptions.)"
    preamble = GROUNDING_PREAMBLE.format(
        wiki_title=wiki_title, wiki_url=wiki_url, wiki_text=wiki_text,
        country=country, ich_block=ich_block,
    )
    return preamble + base


def generate_writeup(country: str, ethnicity: str, region: str, seed_traditions: list[str],
                     wiki: dict | None = None, ich: list[dict] | None = None) -> str:
    """Generate the ethnographic writeup. If `wiki` or `ich` are provided, the
    prompt is prepended with grounding source material; otherwise it falls
    back to the ungrounded prompt (LLM-only, from-memory)."""
    if wiki or ich:
        prompt = make_grounded_prompt(country, ethnicity, region, seed_traditions, wiki, ich)
    else:
        prompt = make_prompt(country, ethnicity, region, seed_traditions)
    return run_claude(prompt)
