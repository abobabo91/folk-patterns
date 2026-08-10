# folk-patterns

A visual atlas of folk-art surface patterns organized by ethnicity, powered by museum open-access data.

Live map: a spinnable dark globe with a marker per ethnicity. Click a marker → per-ethnicity sidebar with a Claude-drafted encyclopedic writeup + every indexed object grouped by art form. Click any object → full detail page showing all provenance data captured from the source museum (dimensions, materials, techniques, gallery number, credit line, IIIF-resolvable image, deep-links to Wikidata and AAT vocab where present).

**Status:** Central Asia prototype (7 countries, 13 ethnicities, 188 objects, 359 image files including alt views). See `library/` for the raw image library.

## How it works

```
data/seed/<region>.json                # hand-written taxonomy (countries × ethnicities × traditions × homeland lat/lon)
  │
  ▼
scripts/scrape_region.py               # queries Met + V&A + Rijksmuseum, canonicalizes, downloads images
  │
  ▼
library/<region>/<country>/<ethnicity>/<art_form>/<tradition>/
  ├─ images/*.jpg
  └─ metadata.json                     # canonical records (full raw museum response preserved in `raw`)
  │
scripts/generate_writeups.py           # Claude CLI drafts per-ethnicity markdown
  │
  ▼
content/<region>/<country>__<ethnicity>.md
  │
scripts/build_index.py                 # aggregates into site-ready shards
  │
  ▼
data/{index,globe}.json                # globe payload + facets
data/ethnicities/*.json                # per-ethnicity page shards
data/objects/*.json                    # per-object detail shards
  │
site/                                  # Astro static site consumes the shards
  │
  ▼
folk-patterns.<domain>                 # deploy target
```

## Quickstart

```bash
# 1. one-time setup
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

cd site && npm install && cd ..

# 2. scrape a region — runs Met + V&A + Rijks + Smithsonian + Cleveland +
#    British Museum + Europeana + Wikimedia Commons in the right order.
python scripts/scrape_all.py central_asia

# 3. draft writeups (Claude CLI must be installed and signed in)
python scripts/generate_writeups.py central_asia

# 4. build the site index shards
python scripts/build_index.py

# 5. run the site (Astro dev on :4321)
cd site && npm run dev
```

Individual scrapers still exist (`scrape_region.py`, `scrape_cleveland.py`,
etc.) for targeted re-runs; `scrape_all.py` is the one-command wrapper.

## Adding a new culture (end-to-end, agentic)

```bash
# existing region
python scripts/add_culture.py --name "Yakut" --country "Russia" --region central_asia

# NEW region — auto-drafts the region seed file first
python scripts/add_culture.py --name "Wayuu" --country "Colombia" \
    --region latin_america \
    --region-display "Latin America" \
    --region-countries "Colombia,Peru,Mexico,Guatemala,Bolivia,Ecuador,Brazil"

# batch / non-interactive
python scripts/add_culture.py --name "Ainu" --country "Japan" --region east_asia -y
```

Runs a Claude-CLI-driven pipeline:
  0. **(new region)** If `data/seed/<region>.json` doesn't exist and
     `--region-display` + `--region-countries` were passed, LLM drafts the
     whole region seed (place_to_country map, reject_places, per-country
     Met gate tokens).
  1. **Draft seed entry** — LLM produces homeland lat/lon, traditions,
     Commons categories, per-museum source_queries. Prints for review.
  2. **Ambiguity probe** — bare-word Europeana search + LLM review of the
     top 20 hits. If off-topic collisions found (saint names, author
     pen-names, language grammar collisions), the LLM's suggested reject
     regex is **auto-appended** to `_AMBIGUOUS_ETHNONYM_REJECT` in
     [europeana.py](src/folk_patterns/museums/europeana.py) (with a diff
     shown + `y/n` unless `-y`).
  3. **Scrape** — invokes [scrape_all.py](scripts/scrape_all.py) which
     runs all 5 wired museums.
  4. **Sample review** — LLM audits 12 random newly-scraped records,
     flags misroutes, and auto-patches europeana.py again if a new reject
     class is found.
  5. **Writeup** — invokes [generate_writeup.py](scripts/generate_writeup.py).
  6. **Index** — invokes [build_index.py](scripts/build_index.py).

Flags to skip individual steps: `--skip-scrape`, `--skip-review`,
`--skip-writeup`, `--skip-index`, `-y` for non-interactive batch mode.

Uses the Claude Code CLI (not the paid API — subscription-covered).

**Places.py is seed-driven.** Each `data/seed/<region>.json` file's
`region_places` block holds the `place_to_country`, `reject_places`, and
`signature_traditions` maps. Adding a new region is a pure data operation —
no `places.py` code edit required.

## Adding a new region

See [`docs/adding-a-region.md`](docs/adding-a-region.md).

## Key design decisions

- **Museum open-access APIs, not paid image services.** V&A + Met + Rijksmuseum + Cooper Hewitt + Smithsonian + Europeana are free, have proper attribution, and their metadata is real. Skip Pinterest / stock.
- **Country-first Met, tradition-first V&A.** Met's `/search?q=` has a silent-fallback bug for niche terms — see `tools/knowledge base/museum open access apis 2026-07.md`. V&A search works properly; results are routed by `_primaryPlace` field to the correct country via `src/folk_patterns/places.py`.
- **Canonical schema.** Every downloaded object is normalized into a single shape (`src/folk_patterns/schema.py`) with the untouched museum response preserved in `raw`. No re-scrape ever needed to recover a dropped field.
- **Rule-based classification.** `classify.py` sorts each object into an art form (textile / garment / architectural / ceramic / jewelry / metalwork / painting-mss / sculpture) and assigns a `pattern_density` score 0-3. No AI needed — museum classifications + medium fields are enough.
- **CLI-first for inference.** Per user preference, per-ethnicity writeups use `claude --print` not the API (subscription is already paid).
- **Static site.** Astro builds to plain HTML + JSON — deployable free to Cloudflare Pages / Netlify / Vercel. No backend.

## Docs

- [`docs/architecture.md`](docs/architecture.md) — data flow, module boundaries
- [`docs/schema.md`](docs/schema.md) — canonical record shape
- [`docs/adding-a-region.md`](docs/adding-a-region.md) — new region playbook
- [`docs/classifier.md`](docs/classifier.md) — art_form and pattern_density rules
- [`docs/museums.md`](docs/museums.md) — per-museum quirks and workarounds

Findings kept in `tools/knowledge base/museum open access apis 2026-07.md`.
