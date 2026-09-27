# folk-patterns

A visual ethnographic atlas — the material culture of the world's peoples, organized by ethnicity, powered by museum open-access data.

## What the collection is

A general ethnographic collection: **everything a people made, wore, built or used that is beautiful and tells you something about them.** Dress and textiles, jewellery, vessels, tools and weapons, musical instruments, furniture and household things, masks and ritual objects, vernacular and monumental buildings, documentary photographs of dress, craft and daily life, the art of the culture's own courts and temples, and the archaeology of its homeland. Every record carries an era — traditional, modern or archaeological — so modern and excavated material sit in their own sections rather than being dropped.

Surface pattern is not the filter. It shows up anyway — in the weaving, the tilework, the carving — and `pattern_density` ([docs/classifier.md](docs/classifier.md)) keeps it available as a facet, not an entry requirement. The repo and bucket keep the name `folk-patterns` for continuity only.

A record belongs when two things hold:

1. **It is of this people.** Made or used within the culture it is filed under. A European artist's picture *of* the culture, a museum catalogue card, a map, or an object the museum itself attributes to a different people does not qualify.
2. **It is a good image of it.** The object or scene is the subject and is clearly visible — not a distant backdrop, not a crop too small or too blurred to read.

What counts as in and out of scope in detail, and how the vetter enforces it: [docs/vetting.md](docs/vetting.md).

Live map: a spinnable dark globe with a marker per ethnicity. Click a marker → per-ethnicity sidebar with a source-grounded culture writeup + every indexed object grouped by art form. Click any object → full detail page showing all provenance data captured from the source museum (dimensions, materials, techniques, gallery number, credit line, IIIF-resolvable image, deep-links to Wikidata and AAT vocab where present).

**Status:** The production site has 10 regions, 100 cultures and 6,353 objects. Edo, Bemba, Kamba, Aymara, Tibetan, Pende and Hausa were added on 2026-09-27, each with a grounded short writeup. Their new images are on R2.

The earlier 17 world-list additions on 2026-09-26 were Tiv, Akan, Ambundu,
Songye, Gbagyi, Mambila, Boya, Chamba, Bwa, Sukuma, Haida, Inuit, Ainu, Rukai,
Naga, Sámi and Māori. They came through `--from-picks` (below).

**Vetting baseline (2026-09-24)** ([docs/vetting.md](docs/vetting.md)): 4,457 kept, 889 dropped, 79 drops re-filed under the culture they actually belong to; 4,380 site objects after one-culture-per-object and picture dedup at that time. Subsequent world-list picks are judged before onboarding. Thin cultures the British Museum facet cannot fill include Qashqai, Sidama, Pamiri, Uzbek (Afghanistan), Oromo and Yakan.

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
scripts/vet_images.py                  # THE QUALITY GATE — shows every image to
  │                                    # Claude, drops what doesn't belong under
  │                                    # its ethnicity, fixes wrong categories.
  │                                    # Writes vision_vetted + the reasoning.
  ▼
scripts/generate_writeups.py           # Claude CLI drafts per-ethnicity markdown
  │
scripts/restructure_writeups.py        # Haiku rewrites it into the short fixed
  │                                    # format (at a glance, ≤5 bullets per
  │                                    # section, glossary); audited against the
  │                                    # original, kept as <name>.long.md
  ▼
content/<region>/<country>__<ethnicity>.md
  │
scripts/reattribute_drops.py           # drops that belong to ANOTHER atlas
  │                                    # culture: named, re-judged, re-filed
scripts/build_index.py                 # aggregates into site-ready shards:
                                       # drops vision_vetted == False (unless
                                       # re-filed), one culture per object,
                                       # picture/accession dedup
  │
  ▼
data/{index,globe}.json                # globe payload + facets
data/ethnicities/*.json                # per-ethnicity page shards
data/objects/*.json                    # per-object detail shards
  │
site/                                  # Astro static site consumes the shards
  │
  ▼
Vercel project folk-patterns           # vercel --prod from site/; images on R2
```

## Quickstart

```bash
# 1. one-time setup
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

cd site && npm install && cd ..

# 2. scrape a region — runs Met + V&A + Rijks + Smithsonian + Cleveland +
#    British Museum + Europeana + Wikimedia Commons in the right order.
#    The British Museum sits behind Cloudflare: point BM_CDP_URL at a Chrome
#    with remote debugging (docs/museums.md#british-museum).
python scripts/scrape_all.py central_asia

# 3. vet every new record (Claude CLI) — build_index keeps unjudged records
python scripts/vet_images.py --target library

# 4. draft writeups (Claude CLI must be installed and signed in)
python scripts/generate_writeups.py central_asia
python scripts/restructure_writeups.py --only Yoruba --preview   # -> work/writeup-preview/
python scripts/restructure_writeups.py                            # all; ~$0.05 each on Haiku

# 5. images to R2, then the site index shards
python scripts/upload_to_r2.py --commit -j 8
python scripts/build_index.py

# 6. run the site (Astro dev on :4321), or deploy it
cd site && npm run dev
cd site && npm run prepare-data && vercel --prod --archive=tgz
```

Individual scrapers still exist (`scrape_region.py`, `scrape_cleveland.py`,
etc.) for targeted re-runs; `scrape_all.py` is the one-command wrapper.

## Adding a new culture (end-to-end, agentic)

From the world list (`docs/world-peoples.md`), with vetted objects — the route new cultures take:

```bash
# candidates: BM + Met + Cleveland, plus Europeana's ethnographic museums (name + country check)
python scripts/world_peoples.py europeana-objects --only Q1235705   # omit --only for every listed people
python scripts/normalize_kinds.py --world                          # new object names -> kind + category
python scripts/world_peoples.py candidates
# judge up to 10 candidates per category (image + QUALITY 1-5), rank; BM needs Chrome on :9226
BM_CDP_URL=http://127.0.0.1:9226 python scripts/world_peoples.py pick --only Q1235705 --shard 0/1
# seed draft + picks into library/ + writeup + shorten + index (no probe, scrape or review)
BM_CDP_URL=http://127.0.0.1:9226 python scripts/add_culture.py --name Tiv --country Nigeria     --region sub_saharan_africa --from-picks Q1235705 -y
python scripts/upload_to_r2.py --commit -j 8     # then build_index.py again
```

The full Europeana search ran for all 568 listed peoples on 2026-09-26. It
yielded 13,944 candidate rows (13,049 distinct Europeana records); the combined
BM, Met, Cleveland and Europeana pool has 84,134 distinct objects. These are
search candidates, not approved site objects. `europeana-objects` appends on
reruns, and `candidates` uses the last row for each people. `pick` also consults
`data/world/pick_exclusions.json` for museum-attribution errors found by hand.
For a kept object's corrected category, add an entry to
`data/world/pick_overrides.json`; `pick` applies it before category ranking.

The next rollout is staged locally: Edo (91 library records, 91 site objects),
Bemba (42 library records, 40 site objects), Kamba (77/77), Aymara (66/66),
and Tibetan (76/76). The Aymara and Tibetan picks finished with the Codex CLI
subscription. The judge cache retained completed Claude verdicts, and only
missing objects were judged. Manual image review removed two weak Aymara
objects, a dead Tibetan image link, and two Met records showing the same armor
installation misclassified as a photograph and a sculpture. Set
`BM_CDP_URL=http://127.0.0.1:9226` for British
Museum images and `FOLK_LLM_BACKEND=codex` to use Codex instead of Claude.
On PowerShell, set these with `$env:BM_CDP_URL='http://127.0.0.1:9226'` and
`$env:FOLK_LLM_BACKEND='codex'` before running the scripts. The fallback uses
`gpt-5.6-luna` at low reasoning effort, an isolated temporary directory, and
the same judge prompt and image size. A three-image check found the same
BELONGS and IMAGE answers as Claude, but Codex scored two weak objects one
point higher; review its new QUALITY 3 objects before onboarding.

Bemba's seed was corrected to remove Makishi and Mukanda, which
[UNESCO attributes](https://ich.unesco.org/en/RL/makishi-masquerade-00140)
to Luvale, Chokwe, Luchazi and Mbunda communities.
The Kamba Wikipedia extract conflates the Kenyan Akamba with Paraguay's Kambá
Kuá. The Paraguay claim was removed from both Kamba writeups; the raw source
sidecar remains unchanged. [Paraguay's culture ministry](https://www.cultura.gov.py/wp-content/uploads/2011/08/Sitios_de_memoria_Kambakua_Telesca.pdf)
describes Kambá Kuá as descendants of the Afro-Uruguayan group that accompanied
Artigas into Paraguay.
The Aymara and Tibetan writeups and sidecars were checked against the museums'
records and UNESCO's pages. UNESCO's Aymara safeguarding programme and three
Tibetan entries missing from Wikidata's country-of-origin query are added as
curated ICH references (`src/folk_patterns/media.py`). The site labels these
records as entries because the Aymara programme is on UNESCO's Register of
Good Safeguarding Practices, rather than an inscription on the Representative
List.
`add_culture.py` now stops before rebuilding the index if grounding, writeup
generation or short-format restructuring fails; the restructuring script
reports a failure through its exit code.

Shan was onboarded from the world list with the Codex CLI subscription. Manual
review of images and original museum catalogues left 34 objects across ten
categories from British Museum and Europeana records; 82 exclusions prevent
the same misattributions and weak items from recurring. The generated long and
short profiles were replaced with source-grounded text, and the media sidecar
was narrowed to four directly relevant Commons images. The local index now
contains 101 cultures and 6,387 objects. All 34 Shan object images are on R2.
Europeana's provider `country` and `dcCreator` had also been imported as
production places. The importer now uses only an explicit place label, and
1,177 existing Europeana records with no such label had that false place
cleared before the index was rebuilt.

Konyak was then onboarded from British Museum world-list picks. Image contact
sheets and all selected museum catalogue pages were reviewed; ten weak,
duplicate, part-only or uncertain-attribution picks were excluded and one
roof-ornament model was moved from architecture to sculpture. The 45 selected
objects span eight categories. The long and short profiles were rewritten
against museum and Nagaland government sources; generic India media results
were removed. All 45 images are on R2; the index contains 102 cultures and
6,432 objects.

When adding the first culture in a country, `majority_ethnicity` stays null.
Set it by hand only when country-only museum records can safely be routed to
that culture. Navajo, Toba, Guna and Bemba are minority peoples in their
countries, so their new country entries have no automatic fallback.

Measured 2026-09-26 on 20 peoples: pick ~$0.40 of judge calls and 2-10 min per people (3 processes in
parallel with `--shard i/3`); onboarding ~4 min per people, the shortened writeup $0.05. A NEW region
needs `--region-display` and `--region-countries` on the first culture.

By scraping:

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
- **Rule-based classification.** `classify.py` sorts each object into an art form (textile / garment / architectural / ceramic / jewelry / metalwork / painting-mss / sculpture) and assigns a `pattern_density` score 0-3. No AI needed — museum classifications + medium fields are enough. The vetter overrides the art form where the image shows it is wrong (`art_form_vision`). `pattern_density` is a facet for browsing by pattern, never a reason to exclude.
- **CLI-first for inference.** Per user preference, per-ethnicity writeups use `claude --print` not the API (subscription is already paid).
- **Static site.** Astro builds to plain HTML + JSON — deployable free to Cloudflare Pages / Netlify / Vercel. No backend.

## Docs

- [`docs/vetting.md`](docs/vetting.md) — the quality gate: how it judges, why it's trusted, how to run it
- [`docs/cloud-vetting.md`](docs/cloud-vetting.md) — running the vetting in a Claude Code cloud session: batch export, cloud procedure, applying verdicts
- [`docs/architecture.md`](docs/architecture.md) — data flow, module boundaries
- [`docs/schema.md`](docs/schema.md) — canonical record shape
- [`docs/adding-a-region.md`](docs/adding-a-region.md) — new region playbook
- [`docs/classifier.md`](docs/classifier.md) — art_form and pattern_density rules
- [`docs/museums.md`](docs/museums.md) — per-museum quirks and workarounds
- [`docs/source-census.md`](docs/source-census.md) — how many unscraped objects each source holds per culture
- [`docs/world-peoples.md`](docs/world-peoples.md) — every people with 30+ image objects under its name in the British Museum, Met and Cleveland, per continent, with category breadth (generated by `scripts/world_peoples.py`; the steps are in its docstring)

Findings kept in `tools/knowledge base/museum open access apis 2026-07.md`.
