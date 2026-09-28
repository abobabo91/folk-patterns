"""Subscription-CLI writeup generator for full-scope ethnographic profiles.

Produces a structured markdown per (country, ethnicity) covering both material
culture (traditionally the museum-object focus of this project) and intangible
heritage (music, dance, festivals, foodways, oral tradition) — a proper
néprajzi/ethnographic profile rather than a pattern-only writeup.

Per CLAUDE.md rule (`Never use a paid LLM API without explicitly asking me first`),
shell out to `claude --print` by default. `FOLK_LLM_BACKEND=codex` uses the
Codex CLI subscription while Claude is limited.

The output is markdown with YAML frontmatter — plays well with Astro Content
Collections but also renders as plain markdown anywhere.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

from folk_patterns.backend import is_limit_error, mark_claude_limited, use_codex

MODEL = "claude-opus-5"


def run_claude(prompt: str, timeout: int = 900) -> str:
    if use_codex():
        from folk_patterns.codex_cli import ask
        return ask(prompt, timeout=timeout)
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
        if is_limit_error((result.stdout or "") + (result.stderr or "")):
            mark_claude_limited(result.stdout or result.stderr)
            return run_claude(prompt, timeout)
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


GROUNDING_PREAMBLE = """You will be given source material below: Wikipedia articles, UNESCO
Intangible Cultural Heritage inscriptions, and the catalogue records of the
museum objects this atlas shows for the people. Write ONLY what these sources
say. Every vernacular term, personal or place name, number and date you write
must appear in the sources; do not add any from your own recollection, even
when you are confident of it. Where the sources say nothing about a section,
write one sentence saying the sources used do not cover it, rather than
filling it in. Paraphrase freely, but add no facts. Cite the Wikipedia URLs
and any UNESCO ICH identifier(s) in the "Sources & further reading" list at
the end.

============ SOURCE 1: Wikipedia ============
{wiki_block}

============ SOURCE 2: UNESCO Intangible Cultural Heritage inscriptions ============
The following ICH elements have {country} listed as a country of origin. Each
has a canonical page at https://ich.unesco.org/en/{{code}}. Reference them by
name in the relevant sections (Music, Dance, Foodways, Festivals, etc.) only
when the inscription itself concerns this ethnic group. In "Sources & further
reading", include the code and URL for any you reference.

{ich_block}

============ SOURCE 3: museum catalogue records of the objects shown ============
{museum_block}

============ END SOURCES ============

Now write the writeup according to the template below.

"""


def museum_records_text(region: str, country: str, ethnicity: str, max_chars: int = 20_000) -> str:
    """The museums' own catalogue text for a culture's library records: title,
    date, medium and description. Never the image judge's `vision_reason`,
    which is model-written (a pick judge called a Tlingit copper "tinneh")."""
    from folk_patterns.util import library_path
    lines = []
    for f in sorted(library_path(region, country, ethnicity).glob("*/*/metadata.json")):
        for r in json.loads(f.read_text(encoding="utf-8")):
            ph, raw, src = r.get("physical") or {}, r.get("raw") or {}, r.get("source") or {}
            desc = [ph.get(k) for k in ("summary", "physical_description", "historical_context")]
            eu = raw.get("dcDescription")
            if isinstance(eu, list):
                desc += eu[:3]
            elif isinstance(eu, str):
                desc.append(eu)
            parts = [ph.get("title"), ph.get("date_text"), ph.get("medium_raw")] + desc
            text = " · ".join(" ".join(str(x).split()) for x in parts if x)
            if text:
                lines.append(f"- [{src.get('museum_name') or src.get('museum')}] {text[:600]}")
    out = "\n".join(lines)
    return out[:max_chars] if out else "(none)"


# Related articles tried besides the people's main one; a missing page is skipped.
EXTRA_ARTICLES = ["Culture of the {e}", "{e} culture", "{e} art", "{e} mythology", "{e} language",
                  "{e} music", "{e} cuisine", "{e} religion"]

_AUDIT_STOP = {"and", "the", "with", "for", "from", "its", "our", "of", "or"}


def _fold(s: str) -> str:
    import unicodedata
    s = s.replace("’", "'").replace("ʼ", "'")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


# A word, keeping inner dots and apostrophes (at.oow, koo.éex', s'áaxw).
_WORD = re.compile(r"[^\W\d_]+(?:[.'’ʼ][^\W\d_]+)*'?")


def _bases(w: str) -> set[str]:
    """The word and each form of it with one English inflection removed."""
    return {w} | {w[: -len(suf)] for suf in ("ings", "ing", "ers", "er", "es", "ed", "s")
                  if w.endswith(suf) and len(w) - len(suf) >= 3}


def unsupported(sources: str, md: str) -> list[str]:
    """Italic terms and numbers in a writeup that the sources do not contain.
    A term passes when each of its words is a whole word of the sources, or
    differs from one only by an English inflection, so "Chilkat robe" passes on
    "Chilkat" and "robes". Whole words, because substrings let "tifa" pass on
    "artifact", "hit" on "white" and "otsj" on "Otsjanep" (2026-09-28)."""
    body = md.split("## Sources")[0]
    words = {w.rstrip("'") for w in _WORD.findall(_fold(sources))}
    bases = set().union(*(_bases(w) for w in words)) if words else set()
    def ok(w: str) -> bool:
        f = _fold(w).rstrip("'")
        return f in _AUDIT_STOP or bool(_bases(f) & bases)
    probs = []
    for t in sorted(set(re.findall(r"(?<![*\w])\*([^*\n]{2,60})\*(?!\*)", body))):
        # fold first: a combining mark splits a word ("nuučaan̓uł", Nuu-chah-nulth 2026-09-28)
        bad = [w for w in _WORD.findall(_fold(t)) if not ok(w)]
        if bad:
            probs.append(f"term not in the sources: {t} ({', '.join(bad)})")
    def ungroup(x: str) -> str:   # "4,000" in a writeup, "4000" in the source (Nuu-chah-nulth)
        return re.sub(r"(?<=\d),(?=\d{3}\b)", "", x)
    plain = ungroup(sources)
    for n in sorted(set(re.findall(r"\b\d[\d,.]*\d\b|\b\d\b", body))):
        if n.strip(",.") not in sources and ungroup(n.strip(",.")) not in plain:
            probs.append(f"number not in the sources: {n}")
    return probs


def make_grounded_prompt(country: str, ethnicity: str, region: str, seed_traditions: list[str],
                         wiki: dict | None, ich: list[dict] | None,
                         extra_wiki: list[dict] | None = None, museum: str = "(none)") -> str:
    """Prepend the sources to the base prompt: Wikipedia (main + related
    articles), UNESCO ICH, and the museum catalogue text.

    `wiki` shape: {title, url, intro, full_text, ...} (from media.wiki_fetch_article)
    `ich`  shape: [{code, title, unesco_url, description}] (from media.unesco_ich_for_country)"""
    base = make_prompt(country, ethnicity, region, seed_traditions)
    if not wiki and not ich and museum == "(none)":
        return base
    # The template's counts and "you're confident about" invite invention; the
    # seed list is itself LLM-drafted (it carried Asmat "wuramon", 2026-09-27).
    base = (base.replace("Seed textile / pattern traditions we already index:",
                         "Search terms we indexed (NOT a source; use one only if the sources name it):")
                .replace("Add other well-documented textiles you're confident\nabout. 4–7 entries.",
                         "Add others only from the sources. Up to 7 entries; fewer when the sources have fewer.")
                .replace("5–10 named motifs.", "Only motifs the sources name; omit this paragraph if they name none."))
    base += ("\n\nEvery count in this template (entries, motifs, words) is a maximum. The sources rule "
             "above overrides any instruction here to be specific or to add what you know.\n")
    return GROUNDING_PREAMBLE.format(
        wiki_block=grounding_wiki_block(wiki, extra_wiki), country=country,
        ich_block=_ich_block(ich), museum_block=museum) + base


def grounding_wiki_block(wiki: dict | None, extra_wiki: list[dict] | None = None) -> str:
    arts = [a for a in [wiki, *(extra_wiki or [])] if a and (a.get("full_text") or a.get("intro"))]
    return "\n\n".join(f'--- "{a.get("title")}" ({a.get("url")}) ---\n{a.get("full_text") or a.get("intro")}'
                       for a in arts) or "(none)"


def _ich_block(ich: list[dict] | None) -> str:
    if not ich:
        return "(none — no UNESCO ICH inscriptions for this country.)"
    return "\n".join(f'- {e["code"]}: "{e["title"]}"' + (f' — {e["description"]}' if e.get("description") else "")
                     for e in ich)


def grounding_sources_text(wiki, ich, extra_wiki=None, museum: str = "") -> str:
    """Everything the writer was shown, as one string for `unsupported()`."""
    return "\n".join([grounding_wiki_block(wiki, extra_wiki), _ich_block(ich), museum])


def generate_writeup(country: str, ethnicity: str, region: str, seed_traditions: list[str],
                     wiki: dict | None = None, ich: list[dict] | None = None,
                     extra_wiki: list[dict] | None = None, museum: str = "(none)",
                     feedback: list[str] | None = None) -> str:
    """Generate the ethnographic writeup from the sources (or, with none,
    ungrounded from memory). `feedback` is a retry: the audit's objections to
    the previous attempt."""
    if wiki or ich or museum != "(none)":
        prompt = make_grounded_prompt(country, ethnicity, region, seed_traditions, wiki, ich, extra_wiki, museum)
    else:
        prompt = make_prompt(country, ethnicity, region, seed_traditions)
    if feedback:
        prompt += ("\n\nA previous attempt was rejected because it used terms, names or numbers that appear "
                   "in none of the sources. Leave every one of them out, or say the same thing in plain "
                   "English without them. Rejected:\n- " + "\n- ".join(feedback))
    return run_claude(prompt)


RESTRUCTURE_MODEL = "claude-haiku-4-5-20251001"

# Headings a restructured writeup must keep: EthnicityPanel.tsx hangs the
# object galleries under them.
RESTRUCTURE_HEADINGS = ["## At a glance", "## Overview", "## Material culture", "### Textile & pattern traditions",
    "### Clothing & dress", "### Architecture", "### Ceramics, metalwork & everyday objects",
    "### Jewelry & body adornment", "## Music & performance", "## Dance & theatre", "## Festivals & rituals",
    "## Foodways", "## Oral tradition & literature", "## Language & religion", "## Glossary",
    "## Sources & further reading"]


def missing_headings(md: str) -> list[str]:
    lines = {l.strip() for l in md.splitlines()}
    return [h for h in RESTRUCTURE_HEADINGS if h not in lines]

SECTIONS = ["Textile & pattern traditions", "Clothing & dress", "Architecture",
            "Ceramics, metalwork & everyday objects", "Jewelry & body adornment", "Music & performance",
            "Dance & theatre", "Festivals & rituals", "Foodways", "Oral tradition & literature",
            "Language & religion"]
_MATERIAL = SECTIONS[:5]

RESTRUCTURE_PROMPT = """Below is an ethnographic profile of the {ethnicity} ({country}) written for a
folk-culture atlas. Extract its content into the JSON shape below, for a
general reader: plain words, short sentences, no academic phrasing.

Rules:
- Use ONLY facts in the profile. Add nothing. Dropping detail is fine.
- "lead": one sentence, the most important thing about that section.
- "items": the up-to-5 MOST DISTINCTIVE named things of that section, each
  {{"name": plain English name, "term": the vernacular term or "", "text": one sentence}}.
  Give items whenever the profile names things for the section.
- Put each item where it belongs: an object under its object section; a
  practice (game, hunt, rite) under Festivals & rituals or Music & performance.
- "glossary": the 15-25 most important vernacular terms that you used as a
  "term" above, each {{"term": ..., "meaning": a few words}}.
- "sources": the profile's source list lines, copied unchanged.

Return only this JSON, nothing else:
{{"glance": {{"who": "...", "where": "...", "how_many": "...", "language": "...", "religion": "...",
             "known_for": ["3-5 signature things"]}},
 "overview": "3-4 sentences",
 "material_lead": "one sentence about the material culture as a whole",
 "sections": {{{section_keys}}},
 "glossary": [...],
 "sources": ["..."]}}

PROFILE
{markdown}
"""


def _render_restructured(front: str, d: dict) -> str:
    """Markdown from the extracted JSON. The format lives here, not in the
    prompt: every heading always present, at most 5 items per section, a
    glossary of at most 25 terms that the text actually uses."""
    g = d.get("glance") or {}
    out = [front.strip(), "", "## At a glance", "| | |", "|---|---|"]
    for label, key in (("Who", "who"), ("Where", "where"), ("How many", "how_many"), ("Language", "language"),
                       ("Religion", "religion")):
        out.append(f"| {label} | {(g.get(key) or 'not stated').strip()} |")
    out.append(f"| Known for | {' · '.join((g.get('known_for') or [])[:5])} |")
    out += ["", "## Overview", "", (d.get("overview") or "").strip(), "", "## Material culture", "",
            (d.get("material_lead") or "").strip()]
    used = set()
    for sec in SECTIONS:
        body = (d.get("sections") or {}).get(sec) or {}
        out += ["", ("### " if sec in _MATERIAL else "## ") + sec, "", (body.get("lead") or "Little is recorded.").strip()]
        items = [it for it in (body.get("items") or []) if (it.get("name") or "").strip()][:5]
        if items:
            out.append("")
        for it in items:
            term = (it.get("term") or "").strip()
            name = it["name"].strip()
            if term and name.lower().startswith(term.lower()):
                # "Aṣọ òkè (cloth of the top country)" + term "aṣọ òkè": keep the gloss as the name
                gloss = re.search(r"\((.+)\)\s*$", name)
                name = gloss.group(1).strip().capitalize() if gloss else name
                if name.lower() == term.lower():
                    term = ""
            if term:
                used.add(term.lower())
            name = name[:1].upper() + name[1:]   # Haiku often lowercases English names
            out.append(f"- **{name}**" + (f" (*{term}*)" if term else "") + f" — {(it.get('text') or '').strip()}")
    text = "\n".join(out).lower()
    gl = [x for x in (d.get("glossary") or []) if (x.get("term") or "").strip() and x["term"].strip().lower() in text][:25]
    out += ["", "## Glossary", ""] + [f"- *{x['term'].strip()}* — {(x.get('meaning') or '').strip()}" for x in gl]
    out += ["", "## Sources & further reading", ""] + [
        (l if l.lstrip().startswith("-") else f"- {l}") for l in (d.get("sources") or [])]
    return "\n".join(out) + "\n"


def restructure_writeup(markdown: str, ethnicity: str, country: str, timeout: int = 900,
                        model: str = RESTRUCTURE_MODEL, thinking: bool = False,
                        effort: str | None = None, feedback: list[str] | None = None) -> tuple[str, dict]:
    """Rewrite an existing writeup into the fixed short format, using only its
    own facts: the model extracts JSON, _render_restructured writes the
    markdown. Returns (markdown or "" on a bad reply, claude json event)."""
    import os
    import re
    import shutil
    import tempfile
    from pathlib import Path
    d = Path(tempfile.mkdtemp(prefix="restructure-"))
    (d / "empty_mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
    cmd = [shutil.which("claude") or "claude", "--print", "--no-session-persistence", "--setting-sources", "local",
           "--model", model, "--output-format", "json", "--tools", "",
           "--strict-mcp-config", "--mcp-config", str(d / "empty_mcp.json")]
    if effort:
        cmd += ["--effort", effort]
    env = dict(os.environ)
    if not thinking:
        env["MAX_THINKING_TOKENS"] = "0"      # a rewrite, not a reasoning task
    keys = ", ".join(f'"{k}": {{"lead": "...", "items": [...]}}' for k in SECTIONS)
    prompt = RESTRUCTURE_PROMPT.format(
        ethnicity=ethnicity, country=country, markdown=markdown, section_keys=keys)
    if feedback:   # a retry: the audit's objections to the previous attempt
        prompt += ("\n\nA previous attempt was rejected because it used words or numbers the profile does "
                   "not contain. Every term and number must appear verbatim in the profile. Rejected:\n- "
                   + "\n- ".join(feedback))
    if use_codex():
        from folk_patterns.codex_cli import ask
        def obj(properties: dict) -> dict:
            return {"type": "object", "properties": properties,
                    "required": list(properties), "additionalProperties": False}
        string = {"type": "string"}
        array = lambda item: {"type": "array", "items": item}
        section = obj({"lead": string, "items": array(obj({"name": string, "term": string, "text": string}))})
        schema = obj({
            "glance": obj({"who": string, "where": string, "how_many": string,
                           "language": string, "religion": string, "known_for": array(string)}),
            "overview": string, "material_lead": string,
            "sections": obj({key: section for key in SECTIONS}),
            "glossary": array(obj({"term": string, "meaning": string})),
            "sources": array(string),
        })
        import time
        started = time.monotonic()
        data = json.loads(ask(prompt, schema=schema, timeout=timeout))
        front = re.match(r"---.*?---", markdown, re.S)
        return _render_restructured(front.group(0) if front else "", data), {
            "total_cost_usd": 0, "duration_ms": int((time.monotonic() - started) * 1000)}
    res = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                         timeout=timeout, cwd=d, env=env)
    try:
        ev = json.loads(res.stdout)
    except json.JSONDecodeError:
        ev = {"result": res.stdout, "is_error": True}
    reply = ev.get("result") or ""
    if ev.get("is_error") and is_limit_error(reply + (res.stderr or "")):
        mark_claude_limited(reply or res.stderr)
        return restructure_writeup(markdown, ethnicity, country, timeout, feedback=feedback)
    m = re.search(r"\{.*\}", reply, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        data = None
    if not data:
        return "", ev
    front = re.match(r"---.*?---", markdown, re.S)
    return _render_restructured(front.group(0) if front else "", data), ev
