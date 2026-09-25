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
- `build_index.py` — walk `library/**/metadata.json`, aggregate by ethnicity, emit site-ready JSON shards. Along the way it:
  - drops records the vetter said NO to, unless `scripts/reattribute_drops.py` re-judged them YES under another culture — those are filed there;
  - files each museum object under one culture only (145 objects sat in the library under two, found by two cultures' searches) — the copy whose country the object's own place text names wins, else the first;
  - collapses duplicates inside a culture's gallery by museum accession number and by picture (`_same_picture`: a 16×16 dHash of the autocontrasted image, plus aspect-ratio and mean-colour gates when the hashes are merely close). Titles are not a duplicate signal — generic ones ("adire", "cloth", "photographic print; album") are shared by dozens of distinct objects, and a title fingerprint once hid 1,470 of 3,744 objects. Hashes are cached in `.cache/image_hashes.json` (gitignored); a cold build hashes ~3,700 images in about a minute, a warm one takes 6 s;
  - writes `object_count` as what the galleries show, so the marker, the panel header and the galleries agree;
  - labels an untitled object (all are V&A) by its `classification` ("Man's costume"), not by its tradition tag.
- `reattribute_drops.py` — recovers vetter drops that belong to another atlas culture (a Shan cloth under Bamar is a NO for Bamar). Text pass names the people, image pass re-judges under that culture; see [vetting.md](vetting.md#re-attributing-drops).
- `build_gallery.py` — legacy static HTML gallery (predates the Astro site). Kept as a no-JS fallback.

**Site (`site/`)**

- Astro static site generator. React island (`GlobeSwitcher`, `MapLibreGlobe`, `EthnicityPanel`) only where interactivity is needed.
- `site/scripts/sync-public.mjs` mirrors what the site reads — `index.json`, `globe.json`, `ethnicities/`, `objects/` — from `../data/` into `site/public/data/` before dev/build, replacing the shard folders so a dropped object loses its page. Nothing else from `data/` (scrape caches, vetting transcripts) is published. `site/public/data/world-countries.geojson` is the site's own file and the only tracked one there. Images are not copied: `build_index.py` writes R2 URLs.
- Deploy: `vercel --prod --archive=tgz` from `site/` (project `folk-patterns`). Without `--archive=tgz` the CLI uploads every shard as its own file; ~9,000 files trip the free tier's "more than 5000" upload limit (`api-upload-free`), which then blocks all uploads for 24 hours (hit 2026-09-24). `.vercelignore` keeps `node_modules`, `.astro`, `dist` and `.env` out. Vercel builds from the uploaded `site/`, where `../data` does not exist, so the uploaded `public/data` is what ships — run `npm run prepare-data` (or `npm run build`) locally first.
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
- **Images are not in the site.** They live in the Cloudflare R2 bucket (`scripts/upload_to_r2.py --commit -j 8`, additive and idempotent) and `build_index.py` writes their public R2 URLs into the shards. Upload new library images before deploying, or their tiles 404.
