# Adding a new region

Every region is a `data/seed/<region_slug>.json` file plus a `scripts/scrape_region.py <region_slug>` run. The pipeline is region-agnostic.

## 1. Draft the seed

Copy `data/seed/central_asia.json` as a starting point. Fill in:

- `region` — kebab-case slug (`southeast-asia`, `west-africa`, `andes`)
- `countries[]` — each with:
  - `country` — display name
  - `met_queries[]` — broad terms the Met search actually understands (country name, dynasty, culture umbrella). Skip niche craft names here — the Met's `/search?q=` has a silent-fallback bug (see `tools/knowledge base/museum open access apis 2026-07.md`).
  - `ethnicities[]` — each with:
    - `name` — display name for the ethnic group
    - `homeland` — `{ lat, lon }` — used for the globe marker. Pick the cultural/historical center, not the country centroid.
    - `homeland_place` — human-readable label of that point (nearest city / valley / oasis)
    - `traditions[]` — the vernacular craft names. This is the moat — no Pinterest search would ever suggest `khan-atlas` or `chakan`. Aim for 5-15 seed terms per ethnicity.

## 2. Update `places.py`

Add your region to `src/folk_patterns/places.py`:

- `place_to_country` — every place string you'd expect to see in V&A `_primaryPlace` mapped to a country in your seed. Include cities, historical regions, and colonial-era names ("Ceylon", "Dutch East Indies") if the museum still uses them.
- `reject_places` — places you know are OUTSIDE your region. This kills false positives (e.g. a `muqarnas` search returning Cairo drawings when you're indexing Central Asia).
- `signature_traditions` — object types so specific they can be attributed even when place data is thin (e.g. `suzani` → Uzbekistan).

## 3. Scrape

```bash
python scripts/scrape_region.py <region_slug> --max-per-tradition 20
```

Expect 5–20 minutes depending on how many traditions × how many countries. First run is slower because caches are cold. V&A does an extra deep-fetch per accepted image; that's the slowest step.

## 4. Draft writeups

```bash
python scripts/generate_writeups.py <region_slug>
```

One `claude --print` call per (country, ethnicity). Roughly 30s each × (ethnicity count). Idempotent — skips existing files unless `--force`.

Review the output. Claude is instructed to omit uncertain content, but check for wrong dates, invented traditions, wrong regional attribution. Edit freely — writeups are plain markdown.

## 5. Rebuild the site index

```bash
python scripts/build_index.py
```

This walks the library and writes `data/index.json`, `data/globe.json`, `data/ethnicities/*.json`, `data/objects/*.json`. The Astro site consumes these directly.

## 6. Verify locally

```bash
cd site && npm run dev
```

Open `http://localhost:4321/`. New markers should appear at the homelands you seeded. Click through a few, verify the writeups render, verify images load.

## 7. Museum-coverage tuning

If a country / ethnicity shows near-zero objects, don't fix it by relaxing filters — check whether the museums we have wired *actually hold this material*. Central Asia example: V&A has 400+ suzani but 2 Kyrgyz records total. That's a real coverage gap, not a bug. Fix by adding a source with better regional depth (Rijksmuseum for SE Asia colonial holdings, Smithsonian NMAI for Indigenous Americas, Turkotek for tribal Turkic textiles).

## 8. Deploy

Static build:

```bash
cd site && npm run build
```

Deploys to any static host. Cloudflare Pages handles the `dist/` output directly.
