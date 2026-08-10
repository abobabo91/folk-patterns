# Add or improve an ethnicity

Reusable pipeline for adding a NEW ethnicity, or filling gaps for an
EXISTING one where museum material exists but our scrapers missed it
because of terminology.

## Prerequisites

- Claude Code CLI installed (`claude --print` works)
- Python env with the project deps installed
- R2 credentials in `tools/vault/vault.toml` under `[apis.cloudflare_r2]`

## The workflow

### 1. Expand queries agentically

```bash
python scripts/expand_queries.py <ethnicity-name> --apply
```

Uses `claude --print` (subscription, no paid API) to propose per-source
queries + tokens + Wikimedia categories. Merges non-destructively into
`data/seed/<region>.json`.

Fields it fills for each ethnicity:
- `traditions` — synonyms, sub-groups, colonial-language variants
- `cleveland_accept_tokens` — narrow ethnonym + sub-group identifiers
- `arch_commons_categories` — Wikimedia Commons signature architecture
- `source_queries.europeana` — colonial-language phrases

Preview without writing: omit `--apply`. Batch multiple:

```bash
python scripts/expand_queries.py uyghur toraja dayak hmong --apply
```

Real result (2026-07-23) for Uyghur added *Yengisar knife*, *Etles silk*,
*Muqam*, *Chagatai manuscripts*, plus Commons categories *Sugong Tower*,
*Old city of Kashgar*, *Afaq Khoja Mausoleum*, plus Europeana phrases in
French (*ouïghour*) and German (*Uigurisch*, *Ostturkestan*).

### 2. Clear stale caches

The scrapers cache raw API responses per-ethnicity so re-runs are fast.
After changing queries, delete the affected caches so the new queries
actually hit the museum APIs:

```bash
python -c "
from pathlib import Path
for src in ('cleveland', 'europeana', 'va', 'smithsonian'):
    d = Path('data/raw') / src
    if not d.exists(): continue
    for pat in ('*uyghur*', '*toraja*', '*dayak*', '*hmong*'):
        for p in d.glob(pat):
            p.unlink(); print(f'deleted {p.name}')
"
```

### 3. Re-scrape the affected ethnicities

Run whichever sources are relevant. For SE-Asia indigenous groups,
Europeana (Tropenmuseum + Musée du quai Branly aggregation) is usually
the biggest lift. For monuments, Commons is the biggest lift. Cleveland
is stronger for Islamic Central Asia, Buddhist SE-Asia, and Latin America.

```bash
# Per-ethnicity re-scrape (use --only to target one)
python scripts/scrape_cleveland.py    --only Uyghur
python scripts/scrape_europeana.py    --only Uyghur --min 999999
python scripts/scrape_commons_arch.py --only uyghur
```

`--min 999999` on Europeana overrides its default "skip if already
has records" gate — useful when re-scraping.

For V&A / Met / Smithsonian tradition sweeps, use `scripts/scrape_region.py`
(reads new traditions from seed automatically).

### 4. Upload new images to R2

```bash
python scripts/upload_to_r2.py --commit
```

Idempotent — skips already-uploaded images. Also required for the site
to actually render the new tiles (dev + prod both read from R2 CDN).

### 5. Rebuild the index + sync to site

```bash
python scripts/build_index.py
(cd site && node scripts/sync-public.mjs)
```

`build_index.py` re-runs the junk filter safety net, tradition-based
dedup, and generates fresh shards. `sync-public.mjs` copies
`data/` into `site/public/data/` for the dev server.

### 6. Verify in the browser

Open http://localhost:4321/ (or the deployed site) and click through
the improved ethnicity. Expected checks:
- Total tile count went up
- Buckets are varied (not all "photo" or "unclassified")
- Tile titles look sensible (no `.jpg` extensions, no raw HTML tags,
  no `[object Object]`)
- Section galleries interleave with the writeup text sections

## Batch results 2026-07-23

Applied to Uyghur, Toraja, Dayak, Hmong:

| Ethnicity | Before | After | Notes                                            |
|-----------|-------:|------:|--------------------------------------------------|
| Uyghur    |      9 |    26 | +40 Commons arch (Sugong / Jiaohe / Afaq Khoja)  |
| Toraja    |      8 |    16 | +13 Europeana (Sa'dan Toraja / Toradja spelling) |
| Dayak     |      5 |    18 | +40 Europeana (Kayan / Kenyah / Ngaju sub-groups)|
| Hmong     |      5 |    10 | +9 Europeana (Miao / Meo variants)               |

Total library grew 1530 → 1631 records.

## When queries don't help

Some ethnicities are genuinely under-collected in Western open-access
museums:

- **Hazara, Pamiri, Bukharan Jew, Uzbek (Afghanistan)** — Western museums
  hold very little. Local museums (Kabul, Dushanbe, Tashkent) rarely have
  open-access APIs.
- **Karakalpak** — the Savitsky State Art Museum in Nukus is the definitive
  collection but has no open API (browseable website only). Requires a
  bespoke scraper if we want their material.

For these, user contributions (`/contribute` page) are the main path forward.
