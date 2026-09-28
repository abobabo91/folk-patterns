# folk-patterns — instructions for Claude

Read this before touching anything. It supersedes any pattern you might infer
from skimming the code.

## What the collection is

A general ethnographic collection, not a pattern atlas: everything a people
made, wore, built or used that is beautiful and informative, filed by
ethnicity. Surface pattern is a browsing facet (`pattern_density`), never a
reason to exclude. The in/out table is in
[docs/vetting.md → Scope](docs/vetting.md#scope--what-the-collection-keeps);
judge every filtering question against it. The name `folk-patterns` is kept
for continuity only.

## Current priority: fill the thin cultures, then new ones

The library has visual verdicts on all 7,678 records as of 2026-09-27. Misfiled drops are re-filed by `scripts/reattribute_drops.py`, and galleries dedup by picture and accession ([docs/architecture.md](docs/architecture.md)). The British Museum is scraped through its "Ethnic group" facet and needs `BM_CDP_URL` for Cloudflare ([docs/museums.md](docs/museums.md#british-museum)).

Next, in order ([docs/vetting.md → Next steps](docs/vetting.md#next-steps)):

- **New cultures:** `world_peoples.py pick` judges and ranks source images; `add_culture.py --from-picks <key>` loads the selection, then runs the library vetter. The scrape route also runs the vetter. Media enrichment runs the Commons vetter. `build_index.py` refuses any library record without a verdict and shows Commons photos only after both model acceptance and an independent editorial image/caption check (`editorial_reviewed: true`). The local index has 112 cultures and 6,801 objects through Tlingit. Run `scripts/_vet_status.py` for current counts; unjudged or editorially unreviewed Commons photos stay hidden.
- **Codex subscription fallback:** Set `FOLK_LLM_BACKEND=codex` to run the seed, writeup, short rewrite and world pick judge through `codex exec` with `gpt-5.6-luna`, low reasoning effort, and no paid inference API. The original Claude cache is reused. Review Codex QUALITY 3 picks before onboarding: a three-image calibration matched BELONGS and IMAGE but gave two weak objects one extra quality point. Always set `BM_CDP_URL` when loading or picking British Museum records; `_load_picks.py` now refuses a partial load without it.
- **Country fallback is explicit:** `majority_ethnicity` routes country-only museum records, so leave it null for a newly added minority culture. `add_culture.py` no longer sets it to the first culture automatically.
- Thin cultures the BM facet cannot fill (Qashqai, Sidama, Pamiri, Oromo, Yakan, Karakalpak, Afar, Cham, Hazara): Cleveland, V&A and Met, the sources with 3–8% drops.
- **Recent cultures:** Armenian has 28 selected records (19 V&A, 2 British Museum, 7 Europeana), source-reviewed long and short profiles and eight UNESCO references. The V&A additions are in `data/world/curated_picks/Q79797.json`, merged with the ordinary pick during load. Lobi has 16 museum objects and eight editorially reviewed Commons photos. Dogon has 70 museum objects and eight editorially reviewed Commons photos. Luba has 45 museum objects and six editorially reviewed Commons photos; the profile and all object attributions received a second review. Fante has 27 museum objects and four editorially reviewed Commons flags; the object set and profile had a second source review. Their object images are on R2. Idoma has 16 museum objects and five editorially reviewed Commons photos; all object images and the museum-grounded profile received a second review. Urhobo has 25 published museum objects and five editorially reviewed Commons photos; a 26th loaded record is marked rejected after its older source note proved uncertain. Fon has 28 published objects and four reviewed Commons photos; three more loaded records were marked rejected after source review. Asmat has 84 published museum objects (89 loaded; British Museum and Europeana) and eight editorially reviewed Commons photos; its generated profile invented vernacular terms, so both profiles were rewritten from Wikipedia and the museum records only. Tlingit has 30 museum objects and four reviewed Commons photos, and its profiles were rewritten the same way. Check every vernacular term in a generated profile against the sources. The index has 112 cultures and 6,801 objects.


## The one command for everything

**Adding cultures and regions goes through `scripts/add_culture.py`.** Do not
run the individual scrapers, do not hand-write seed entries, do not edit
`src/folk_patterns/places.py` — the orchestrator handles all of it via
Claude CLI (`claude --print`, subscription-covered per user's global rules).

```bash
# Add one culture to an existing region:
python scripts/add_culture.py --name "Yakut" --country "Russia" --region central_asia

# Add a culture to a BRAND NEW region (LLM drafts the region seed first):
python scripts/add_culture.py --name "Wayuu" --country "Colombia" \
    --region latin_america \
    --region-display "Latin America" \
    --region-countries "Colombia,Peru,Mexico,Guatemala,Bolivia,Ecuador,Brazil"

# Batch mode — no confirmations, patches auto-applied:
python scripts/add_culture.py --name "Ainu" --country "Japan" --region east_asia -y
```

The pipeline: draft seed entry → ambiguity probe → **auto-patch europeana.py
with any LLM-suggested reject regex** → run all 5 wired museums via
`scrape_all.py` → sample-review the results → auto-patch again if needed →
generate writeup → rebuild index.

If the user asks to "scrape a whole new region", pass all three of `--name`,
`--region-display`, and `--region-countries`. That triggers step 0 (LLM
drafts the region seed) before step 1 (the ethnicity seed).

## Things NOT to do

- **Do NOT edit `src/folk_patterns/places.py`.** It's seed-driven — region
  data lives in `data/seed/<region>.json` under `region_places`. Editing
  places.py by hand will be overridden on the next run.
- **Do NOT hand-write reject regexes into europeana.py.**
  `add_culture.py` writes them automatically after each ambiguity probe /
  sample review. If a new contamination class shows up, run
  `scripts/_probe_ambiguity.py` or `scripts/_review_sample.py` and let the
  auto-patcher commit the pattern.
- **Do NOT create a new `scripts/scrape_*.py`** unless you're wiring a new
  museum. The 5 that exist cover Met, V&A, Rijks, Smithsonian, Cleveland,
  British Museum, Europeana, Wikimedia Commons.
- **Do NOT use paid inference APIs.** LLM calls go through the Claude CLI by
  default, or the Codex CLI subscription with `FOLK_LLM_BACKEND=codex`.
  See `scripts/_llm.py` and `src/folk_patterns/codex_cli.py`.

## Directory layout (only what matters)

- `data/seed/<region>.json` — the source of truth. Contains region_places,
  countries, ethnicities. Adding data here IS adding cultures.
- `library/<region>/<country>/<ethnicity>/<art_form>/<tradition>/{images/,
  metadata.json}` — canonical output. Never edit by hand; regenerated by
  scrapers.
- `content/<region>/<country>__<ethnicity>.md` — Claude-drafted writeups.
- `scripts/add_culture.py` — the entry point.
- `scripts/_llm.py`, `_draft_seed_entry.py`, `_probe_ambiguity.py`,
  `_review_sample.py`, `_generate_region.py`, `_patch_europeana_reject.py`
  — internal helpers of `add_culture.py`. Underscore prefix = don't call
  directly unless debugging.
- `scripts/_quality_audit.py`, `_completeness_report.py` and
  `_vet_status.py` — read-only diagnostics you can run any time.
  `_vet_status.py` is the one that answers "what has been vetted and what
  hasn't" — always run it instead of trusting a number written in a doc.

## Preventive filters that already exist

If you find a new contamination class (Italian saint names for "San",
Estonian grammar collision for Bamar, French cartoonist "Cham", Dutch
"chin." for Chin, etc.), `add_culture.py`'s sample-review step will
auto-append the pattern. If you spot one outside the pipeline, use
`scripts/_patch_europeana_reject.py --ethnicity <slug> --pattern '<regex>'`
rather than editing europeana.py by hand.

Known filter families in `src/folk_patterns/museums/europeana.py`:
- `_AMBIGUOUS_ETHNONYM_REJECT` — per-ethnicity title/desc regex
- `_HOSTILE_PROVIDER_BY_ETHNICITY` — provider blocklist (Bamar/Estonian)
- `_GENERIC_NONARTIFACT_TITLE` — museum inventories, academic papers
- `NON_CULTURAL_PROVIDER_TOKENS` — SSOAR, GESIS, natural history museums
- Anti-audio (`edmIsShownBy` URL check) and anti-map (`dcType`) filters

`src/folk_patterns/museums/british_museum.py` has `_BM_ETHNONYM_REJECTS`
for BM-specific author/pen-name collisions.

`src/folk_patterns/museums/rijks.py` has `_BLOCKED_RIJKS_TRADITIONS` for
tradition-name Dutch-word collisions (currently just "hol").

## Global validation utility

`src/folk_patterns/util.py::download_image` validates magic bytes on every
downloaded file. Non-image responses raise `ValueError` so the caller can
log_reject. Do not remove this check — it's the only guard against media
URLs that serve MP3s or PDFs with `image/jpeg` Content-Type.
