# Architecture

Four-stage pipeline. Each stage writes to disk; each downstream stage reads from disk. Stateless.

```
┌──────────────┐   scrape_region.py   ┌───────────────┐
│ data/seed/*  │ ─────────────────▶   │ library/      │
│  (taxonomy)  │                      │  (images +    │
└──────────────┘                      │   canonical   │
                                      │   metadata)   │
                                      └───────┬───────┘
                                              │
              generate_writeups.py    ────────┼──────────
                                              │        │
                                              ▼        ▼
                            ┌─────────────────┐  ┌─────────┐
                            │ content/*.md    │  │ raw     │
                            │  (per-ethnicity │  │  cache  │
                            │   writeups)     │  └─────────┘
                            └────────┬────────┘
                                     │
                            build_index.py
                                     │
                                     ▼
                            ┌───────────────────┐
                            │ data/index.json   │
                            │ data/globe.json   │
                            │ data/ethnicities/ │
                            │ data/objects/     │
                            └────────┬──────────┘
                                     │
                            site/ (Astro)
                                     │
                                     ▼
                            static HTML + JSON
```

## Modules

**Python side (`src/folk_patterns/`)**

- `schema.py` — canonical record shape + transformers from each museum's native format. `from_met`, `from_va`, `from_rijks_linked_art`.
- `places.py` — per-region place→country routing map + reject list. Kills the "muqarnas in Cairo returned for an Uzbek query" class of bug.
- `classify.py` — rule-based art_form + pattern_density from `object_type` / `medium` / `title` fields.
- `museums/{met,va,rijks}.py` — API clients with per-museum retry / caching / fallback-detection logic.
- `util.py` — rate-limited HTTP client, image download with dedup, canonical metadata append with idempotent per-record-id dedup.
- `writeup.py` — Claude CLI subprocess helper + the prompt template.

**Scripts (`scripts/`)**

- `scrape_region.py` — the orchestrator. Loops seed countries → Met country-gated scrape; loops seed traditions → V&A tradition-routed scrape.
- `generate_writeups.py` — iterate (country, ethnicity), shell out to `claude --print`, save markdown.
- `build_index.py` — walk `library/**/metadata.json`, aggregate by ethnicity, emit site-ready JSON shards.
- `build_gallery.py` — legacy static HTML gallery (predates the Astro site). Kept as a no-JS fallback.

**Site (`site/`)**

- Astro static site generator. React island (`GlobeSwitcher`, `MapLibreGlobe`, `EthnicityPanel`) only where interactivity is needed.
- `site/scripts/sync-public.mjs` copies `../data/` and `../library/` into `site/public/` before dev/build. Prevents symlink pain on Windows.
- Pages: `/` (globe landing), `/object/[id]` (per-object detail).

## Caching layers

All caches live under `data/raw/` and are safe to delete (a re-scrape will rebuild):

- `data/raw/met/<query>.json` — Met search result IDs per country/culture query.
- `data/raw/va/<tradition>.json` — V&A search snippet arrays per tradition.
- `data/raw/va-deep/<systemNumber>.json` — V&A deep-fetch full records. This is the expensive cache — deleting it forces a re-fetch of every indexed object's 60-field record.
- `data/raw/rijks/<query>.json` — Rijksmuseum LOD IDs per description query.

Image downloads in `library/**/images/` are also idempotent by filename — re-runs skip existing files.

## Non-goals

- **No SSR for map interactivity.** The React globe is a client-only island; Astro doesn't try to SSR MapLibre.
- **No database.** JSON files all the way. `build_index.py` writes ~1000 small files instead of an SQLite blob; git-diffable, greppable, easy to inspect.
- **No CDN for images (yet).** Images are served from the local `library/` copied into `site/public/library/`. For deployment, either upload to a bucket + rewrite paths in `build_index.py`, or let the static host serve them (Cloudflare Pages fits ~1GB total fine).
