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

**Status:** The index has 11 regions, 460 cultures and 17,871 objects (2026-10-04). Maya, Betsimisaraka and 8 peoples rescued by new source rules and a higher per-category limit (Shuar, Kiga, Diola, Wichita, Cherokee, Choctaw, Rizeigat, Ambonese) were added on 2026-10-04, written and reviewed by local Codex. The 237 before them came from pick batch p003 (`docs/vetting.md` → "Pick coverage"), each with at least 5 vetted objects; their profiles were written from sources only (57 in a cloud session, 180 by local Codex) and pass the term-and-number audit. 25 queue peoples still keep fewer than 5 objects and stay in `data/world/pick_coverage.jsonl` for a later round. The onboarding queue is `data/world/onboard_queue.json` (nations such as Japanese or French and umbrella names that duplicate atlas cultures are skipped, with the reason). The site publishes 1,820 editorially reviewed Commons photos (the last 46 that had failed to download were judged by local Codex on 2026-10-05: 28 accepted, 25 of them passed the editorial check). Object images are on R2. The site is https://folk-patterns.vercel.app.

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

# 4. draft writeups from Wikipedia (main + related articles), UNESCO ICH and the museum records only;
#    terms/numbers found in no source are sent back once, leftovers logged to data/writeup_audit.jsonl
python scripts/generate_writeups.py central_asia
FOLK_LLM_BACKEND=codex python scripts/generate_writeups.py --stubs [--limit 5]   # Wikipedia-only, unreviewed-only cultures
# For a Claude Code cloud session, export prompts locally and import the already-shortened results later:
python scripts/generate_writeups.py east_asia --only Ainu --force --export-batch w001
python scripts/generate_writeups.py --import-batch w001 --force  # after the cloud run; see docs/cloud-vetting.md
python scripts/restructure_writeups.py --only Yoruba --preview   # -> work/writeup-preview/
python scripts/move_culture.py east-asia__russia__bashkir europe --commit   # region change: library, R2 keys, content, territories, seed
python scripts/restructure_writeups.py                            # all; ~$0.05 each on Haiku
python scripts/audit_profile.py --region north_america --only Hopi --fix   # short profile vs sources; add_culture runs it

# 5. images to R2, then the site index shards
python scripts/upload_to_r2.py --commit -j 8
python scripts/build_index.py

# 6. run the site (Astro dev on :4321), or deploy it
cd site && npm run dev
cd site && npm run prepare-data && vercel --prod --archive=tgz
```

Individual scrapers still exist (`scrape_region.py`, `scrape_cleveland.py`,
etc.) for targeted re-runs; `scrape_all.py` is the one-command wrapper.

## World-peoples census and unreviewed-only cultures

The resumable `world_peoples.py` census keeps multilingual Wikidata labels,
museum counts, classification, source evidence, and visible gaps in
`data/world/`. Run the steps in this order when refreshing the census:

```bash
python scripts/world_peoples.py wikidata
python scripts/world_peoples.py labels
python scripts/world_peoples.py bm
python scripts/world_peoples.py aliases
python scripts/world_peoples.py europeana
python scripts/world_peoples.py europeana --multilingual
python scripts/world_peoples.py local
python scripts/world_peoples.py classify --backend codex --all-min-sitelinks 20
python scripts/world_peoples.py harvest --threshold 1
python scripts/world_peoples.py report
python scripts/world_peoples.py europeana-objects
python scripts/normalize_kinds.py --world
python scripts/world_peoples.py candidates
FOLK_LLM_BACKEND=codex python scripts/world_peoples.py local-audit   # then candidates again
FOLK_LLM_BACKEND=codex python scripts/world_peoples.py screen
python scripts/unvetted.py resolve
python scripts/unvetted.py places
python scripts/unvetted.py build
python scripts/build_index.py
python scripts/world_peoples.py gaps
FOLK_LLM_BACKEND=codex python scripts/territories.py fetch|ids|match|judge|build
```

`report` lists a people for visual picking only after the normal category
rule. A classified people with at least one BM, Met/Cleveland, or multilingual
Europeana evidence record (`UNVETTED_MIN_EVIDENCE`) can instead be marked `unvetted_only`, unless
`data/world/onboard_queue.json` marks it as an umbrella or duplicate. Those
cultures receive quiet globe markers and text-matched museum candidates, but
no vetted objects or facets; their writeup comes from Wikipedia alone (Stub
writeups below). `unvetted.py build` creates a quiet stub
shard when no existing atlas ethnicity matches, placed as described under
Stub placement below.
Every living people gets a stub, also with no objects: `world_peoples.py
screen` has local Codex judge each classified people not yet on the map, in
batches of 30 per country with the names already on the map and the other
candidates of that country as context, as keep / extinct / duplicate /
not_people (`data/world/screened.json`, raw replies in `screen_raw.jsonl`).
On 2026-10-05 it judged 967: 865 keep, 50 extinct, 38 duplicate, 14 not a
people. A kept people with no resolvable image becomes a zero-object stub
("No museum objects found yet"); 857 of the 1,471 stubs were such. `candidates`
adds the screened peoples to the `peoples.json` rows (they were never in it, so
their harvested BM objects never became candidates: Kota had 25) and takes the
country from `classified.json`, which is corrected by hand. A stub once on the
map stays there when its objects go (`unvetted.py build`). Two BM checks run in
`candidates`: an object whose department (the id prefix: `E_Af`, `E_Am`,
`E_As`, `E_Oc`, `E_Eu`) is on another continent than the people is dropped,
with Indonesia/Philippines/Malaysia, North Africa and Russia/Turkey/the
Caucasus allowed both neighbouring departments; and `_BM_NAME_WRONG` refuses
alias-pass spellings checked by hand to be another people (Nzema "Zimba",
Sihasapa "Blackfoot", Lodha "Lozi"). Run of 2026-10-06: 20 stubs gained
objects (Pondo 451, mostly photographs; Yomut 107; Ngāpuhi 93; Sicangu 55;
Kota 25 reliquary figures), the subgroup taking them from its umbrella (Pondo
from Nguni 463 -> 39); wrong objects left Lodha (234 Lozi), Kotas (25 Kota
of Gabon), Teke (Congo, Turkmen Teke), Catawba, Nara, Toubou and the
colonial-era French, Spanish and Dutch rows. The Kotas still show 15 Met
paintings of the Kota school of Rajput painting, named after the city.
`local-audit` (local Codex, 40 pairs per call) judges each distinct pair of
people and Met/Cleveland culture text once as keep / drop / uncertain
(`data/world/local_verdicts.json`, raw replies `local_audit_raw.jsonl`), and
`candidates` leaves out the objects of a drop. Rows matched by place on purpose
(no culture text) are not judged, and a drop does not count for a
`_PLACE_PEOPLES` people when the text names one of its own places: the judge
called "Bengal, Kolkata, Kalighat" a place, but Kalighat paintings are Bengali.
Run of 2026-10-06: 952 pairs, 532 keep (6,886 objects), 209 uncertain (619,
kept: "probably German", "Armenian or Georgian"), 211 drop, 69 of them
effective (137 objects). Among them: Milan, an Iraqi people, had 49 Italian
Milanese pieces; the Kotas the 15 Kota-school paintings; Hän (Canada) Chinese
Han objects; Sinixt Great Lakes and Lake Van objects; Massachusett objects of
towns in Massachusetts. Moved objects went to the right culture (Kota of Gabon
25 -> 40 with the Met's "Kota peoples, Mbete group"; Italians +9), and the Lu
Mien Yao objects made a new stub, Yao (China). Two samples of 30 and 25
Europeana objects read by hand were attributed right apart from about one in
ten weak or wrong items (a coin under Vikings, a 1940 sports photograph under
Swedes, a Templo Mayor photograph under Yaqui), so Europeana has no extra
filter. BM spellings shared by two peoples with BM objects: only Jarawa
(India 107, Afizere of Nigeria 3), which the department check keeps apart.
Matching reads only vetted
shards: `build_index.py` writes the stubs into `data/ethnicities/` too, and a
people matched against its own stub would drop out of `stubs.json` on the next
build. Country names that differ from `world-countries.geojson` (United States,
Tanzania, North Macedonia, Côte d'Ivoire, Serbia) are aliased in
`unvetted.py`; Samoa and Tonga, absent from the 1:110m polygons, have fixed
points.

State on 2026-10-05 (`report --threshold 6`): 1,335 peoples with evidence,
580 listed, 755 unreviewed-only. 614 of those have at least one resolvable
image and appear as stub cultures (Nzema left on 2026-10-05: its 5 British Museum
objects came from the Wikidata alias "Zimba", a different people, and its
country was misclassified as Guinea-Bissau; both are corrected in
`data/world/`); the site carries 70,032 unreviewed
objects, 2,269 of them in "Other, uncategorised" after the object names were
mapped to kinds by local Codex (`FOLK_LLM_BACKEND=codex python scripts/normalize_kinds.py --world`,
5,682 names on 2026-10-05; before it 10,513 were uncategorised). `docs/gaps.md` shows Europe
at 214 living peoples, 113 with evidence, 73 on the site, and South Asia at
116, 80, 62. The rest have no
record under any of their names in the current sources.

Regions: the Caucasus (Armenia, Georgia, Azerbaijan, the North Caucasus) is its
own region since 2026-10-05. Before that the classifier's free text "Asia" fell
through to East Asia for Georgia, Azerbaijan, Iraq, Lebanon and Cyprus, and
existing shards then carried the error forward to new stubs of the same country.
`unvetted.py` now maps those countries explicitly (`_COUNTRY_REGION`) and
`_site_region` sends "caucasus" and West Asia / Anatolia / Iran text to their
regions. Armenian (from MENA) and Bashkir (East Asia to Europe) were moved with
`move_culture.py`, which also rewrites `local_path` and `cultural.region` in the
library metadata; its images were uploaded to R2 under the new keys and the old
keys left in the bucket. 12 stubs changed key in the same rebuild.

Stub writeups: `generate_writeups.py --stubs` writes one per stub culture from
its English Wikipedia article plus related articles (sitelinks of its Wikidata
item, titles cached in `data/world/stub_wiki_titles.json`); a stub without an
article is skipped and logged. The same term-and-number audit as the vetted
writeups runs with one retry (`data/writeup_audit.jsonl`, `"stub": true`).
Thin articles make the model fill sections with "the sources do not describe
X"; `_drop_uncovered` cuts such sentences and trailing "but they do not name
Y" clauses, then drops sections left empty. A clause starting with "and" is
left alone: cutting it removed real content ("do not offer pork" in Hui).
Run of 2026-10-05 on local Codex, 3 workers: 610 written in 3 h 14 min (15:38-18:52), none
skipped, 7 audit objections all fixed by the retry, 0 unsupported terms left;
the filter changed 527 files (3.62 M to 3.47 M characters) and 53 mixed
sentences mentioning a gap remain. The panel shows the writeup above the
unreviewed candidates. These writeups are long-form and are not run through
`restructure_writeups.py`: tried on Bai, Khevsurians and Amis (Codex,
`--preview`), its fixed section list puts back "The profile does not describe
Khevsurian architecture" for every section the article does not cover, and
Khevsurians grew from 2,489 to 4,169 characters.

Stub writeups run 2 (2026-10-05/06, the stubs added by the screen step): 860
written, 32 Wikipedia fetches rate-limited on the first pass and written on a
rerun; all 1,471 stubs have a writeup. `_drop_uncovered` was then re-applied to
every stub writeup, including the reading-list form of the remark ("- Museum
catalogue records: no object records were provided in the sources used").

The vetted writeups went through the same filter on 2026-10-06 with
`_drop_uncovered(keep_mixed=True)`: a sentence that also carries content ("do
not document X, but they describe Y") stays whole, and a following "They ..."
left without its subject becomes "The sources ...". Without `keep_mixed` the
dry run cut "but they describe diamond-shaped scarification marks" (Punu).
559 of 924 vetted writeup files changed, 9.70 M to 9.32 M characters.

Stub photos: `stub_commons.py` gives the zero-object stubs Wikimedia Commons
photos. `gather` (no LLM) takes the people's Commons category from Wikidata
P373, its subcategories whose names point at costume, craft, art or culture,
and the images in its Wikipedia article, up to 15 per culture. `review` puts
one culture's candidates on one numbered contact sheet and asks local Codex
once, with `commons_editorial.py`'s criteria plus a rule against graphic scenes
(a Jek wedding's slaughter photos passed without it) and everyday snapshots.
`dedupe` drops kept photos whose 8x8 average hash is within 6 bits of an
earlier one (one picture under two file names, tif/jpg pairs). The panel shows
them as "Photographs (Wikimedia Commons, unreviewed)". Following every
subcategory was tried and dropped: for Avars it returned 13 of 20 images of
politicians. Run of 2026-10-05/06: 857 cultures gathered (128 rate-limited
on 4 workers, done again on 1), 705 with candidates, 4,593 images; 2,796
kept by the sheet check, 70 near-duplicates dropped; 589 stubs now show 2,726
photos.

```bash
python scripts/stub_commons.py gather
FOLK_LLM_BACKEND=codex python scripts/stub_commons.py review
python scripts/stub_commons.py dedupe
```

Stub placement: `unvetted.py places` asks local Codex for each stub's homeland
point and caches it in `data/world/stub_places.json`. Against the stubs with a
Wikidata coordinate it was a median 113 km off (62 checked) where the country
centroid was 325 km. A point is used when it lies inside the country's polygon
or within 500 km of it (1,500 km for India, whose polygon lacks the Andaman and
Nicobar Islands), or the country is in `_OVERSEAS`; a point that only fits
with its latitude negated is flipped (Codex returned Angola's Ovimbundu at
12.8 N). On 2026-10-05 608 of 615 points were used. A distance to the centroid
is no test: Russia's lies in Siberia, 3,000+ km from the Kalmyks. Without a
point, a European Russian people goes to a European Russia point, a North
Caucasus people to a Caucasus point, anything else to the jittered centroid.
`build_index.py` then pushes apart markers that would still cover each other
(golden-angle spiral, 0.3 degrees between stubs, 0.15 between vetted cultures;
vetted placed first, then stubs by Wikipedia language editions so a crowded
area keeps its large peoples in place). With 1,471 stubs, 0.6 degrees and
alphabetical order pushed Dagestan's small peoples up to 309 km out and the
Lezgins into the Caspian; 0.3 and size order move 215 stubs, at most 120 km
(Tindi), and leave the Lezgins where they are (2026-10-05).
On the map a hovered marker grows 1.8x and turns opaque, and an invisible
9 px hit layer takes clicks, since unreviewed dots are only 2.5 px. Europeana tiles load through Europeana's thumbnail
service: Finnish Heritage Agency originals answer 401. The Met sits behind
Imperva, which blocked a long `resolve` run after ~2,000 requests; `resolve`
throttles Met calls, leaves blocked ones uncached and starts a fresh client.

Home areas: hovering a marker shades the culture's approximate area faintly,
selecting it shades it stronger (`data/territories/<key>.json`, one GeoJSON
Feature each, fetched on first use). The 3D globe draws the same area as an
outline (drei `Line`, 2 px over a dark underlay: a 1 px line disappeared under
the marker halos around the Yoruba).
`scripts/territories.py` builds them from three free sources: Asher & Moseley
2007 speaker areas via Glottography (CC BY 4.0, glottocodes), Native Land
Digital (CC0, the Americas and Oceania; the full GeoJSON downloaded without an
API key on 2026-10-05, the search endpoint needs one) and GREG (Atlas Narodov
Mira 1964). Candidates come from name matches and from the glottocodes of the
languages Wikidata lists for the people (P103, P2936); those include contact
and national languages (Votes -> Estonian, Russian; Swazi -> Afrikaans), so
local Codex picks the polygons that are the people's own and also draws an
ellipse, used when nothing fits. On 2026-10-05: 1,052 of 1,075 cultures had a
Q-id, 391 a glottocode; 794 had candidates; 732 got source polygons, 343 an
ellipse; 2.2 MB in all (Russians the largest file, 251 KB). Two samples of 20
and 25 were read before the full run and the picks held up (Dogon and Miwok
dialect areas merged, contact languages rejected). A naive name match alone
reached 684 of 1,075.

Every area must sit around its marker (`MAX_OFF_KM` = 150). In `build`, a
polygon farther away gives way to the ellipse when the ellipse is near the
marker (19 on 2026-10-05: Dayak got only Malayic Dayak, Malay only
Kedah-Perak); when polygon and ellipse agree and the stub marker is the odd
one out, the marker moves to the polygon via `stub_places.json` (source
`territory`; Tapirapé, Xerente, Aweer and three more), and an ellipse far off
is centred on the marker. Two vetted seed homelands had a dropped minus sign
(Huastec in Myanmar, Gogo in Ethiopia) and were corrected; a check of all 460
seed homelands against their country polygons found no other. After this all
1,075 areas are within 150 km of the marker, 821 contain it. Nzema stays odd:
its evidence is a BM "Zimba" record and its country Guinea-Bissau.

The Astro dev server dies with EMFILE once `data/` is synced (about 19,000
files). Check the site with `npx astro build` and
`python -m http.server 4400 --directory site/.vercel/output/static` instead.
Serve from outside the output folder, or the next build cannot clear it.

Two source routes searched on 2026-10-04 and their limits:

- `harvest --threshold 1` fetches BM objects for every people with one BM
  record, not only those with 30+: 208 more peoples, 507 objects.
- BM search by production place does not work by name. `place=India` and
  `place=Punjab (India)` answer, but `place=Gujarat`, `Gujarat (state)`,
  `Bengal (region)`, `Kashmir (region)`, `Sindh (province)` and
  `Rajasthan (state)` return nothing, unknown parameters (`place_name`,
  `production_place`) are ignored and return the unfiltered first page, and
  the result teasers carry no production place. South Asian places come from
  the Met and Cleveland culture field instead (`_PLACE_PEOPLES`).

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

**What the pick has looked at.** A candidate is loaded only if the judge kept
it; anything else is never loaded. Every judge reply is cached in
`data/world/picks/raw*.jsonl`, and every candidate that ends without a verdict
goes to `data/world/picks/ledger*.jsonl` with why: `source_rule` (the museum
names several peoples, or marks the attribution `(?)` — the reason names
them), `no_image`, `fetch_failed`, `photo_of_object`, `in_library`, or
`awaiting_judge` (fetched and ready, but the run made no judge call). Both are
gitignored and live on the machine that ran the pick.

```bash
python scripts/world_peoples.py coverage [--only KEY ...]   # per people: kept / judged_drop / review_excluded / source_rule / ... / not_reached
BM_CDP_URL=http://127.0.0.1:9226 python scripts/world_peoples.py pick --cached-only --only KEY ...   # record outcomes, no judge call, pick file unchanged
```

`coverage` without `--only` rewrites `data/world/pick_coverage.jsonl` (one line
per candidate: key, category, source, id, status, reason), which is committed,
so the record survives the gitignored logs. `not_reached` is a candidate the
pick never tried: the 10-per-category cap, or a BM candidate in a run without
`BM_CDP_URL`. A judged candidate is not judged again: reruns read the cache.

**Unreviewed objects.** Culture pages can show a separate set of museum
candidates matched to a people by text only. For a picked culture, the section
remains collapsed; for an `unvetted_only` culture it is the only content and
opens by default. Resolve and build it with
`python scripts/unvetted.py resolve --only KEY` and
`python scripts/unvetted.py build`; images are hotlinked from the museums and
these candidates are not counted in the vetted object totals.

The 2026-09-29 replay over the 155 picked peoples, and what it found about
the "multiple peoples" rule, is in `docs/vetting.md` → "Pick coverage".

The 2026-09-26 rollout added Edo (91 library records, 91 site objects),
Bemba (42 library records, 40 site objects), Kamba (77/77), Aymara (66/66),
and Tibetan (76/76). The Aymara and Tibetan picks finished with the Codex CLI
subscription. The judge cache retained completed Claude verdicts, and only
missing objects were judged. Manual image review removed two weak Aymara
objects, a dead Tibetan image link, and two Met records showing the same armor
installation misclassified as a photograph and a sculpture. Set
`BM_CDP_URL=http://127.0.0.1:9226` for British
Museum images. Every model call picks its backend automatically (`src/folk_patterns/backend.py`): Claude until its subscription usage reaches 90% of the 5-hour or weekly limit (`FOLK_CLAUDE_MAX_PCT`), then the Codex CLI; a Claude "hit your limit" reply also switches, until the reset. `python src/folk_patterns/backend.py` prints the current usage and backend. `FOLK_LLM_BACKEND=codex` or `=claude` forces one.
On PowerShell, set these with `$env:BM_CDP_URL='http://127.0.0.1:9226'` and `$env:FOLK_LLM_BACKEND='codex'`. The fallback uses
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

Armenian was added from nine previously selected British Museum and Europeana
records plus nineteen V&A objects curated from 46 photographed Armenia-place
results. Contact sheets, full museum descriptions and the Codex subscription
judge were reviewed before loading. The 28 selected records include embroidery,
dress, silverwork, a jug, church objects and documentary photographs. Four
Europeana full-size URLs returned 401; their 400-pixel fallback images loaded.
The long and short profiles were rewritten against V&A catalogues and UNESCO.
Wikidata's country-of-origin query missed all eight Armenian UNESCO entries,
so they are supplemented from UNESCO's [Armenia list](https://ich.unesco.org/en/state/armenia-AM?info=elements-on-the-lists).
The generated Commons sidecar contained unrelated maps and Persian material;
all twelve photos were removed. The local index has 103 cultures and 6,460
objects. All 28 Armenian images were uploaded to R2 and the new site was deployed.

Lobi was added under Ghana from British Museum and Cleveland picks. Of 31
Codex-judged candidates, 16 remain after contact-sheet review: weak or
repetitive objects were excluded, and one figure was removed because the
British Museum attributes it to Lo Willi and Dagari as well as Lobi. The
short and long profiles were rewritten against museum, academic, and music
archive sources. They distinguish Lobiri from Birifor and describe `bateba`
as shrine figures associated with living guardian spirits, not ancestral
portraits. Eight of twelve model-accepted Commons images passed a separate
image-and-caption check; the other four are hidden. Four generic or unrelated
Smithsonian music links were removed. The 16 object images are
on R2, and the new site was deployed. The Ghana map pin is a regional anchor:
many objects and communities are in Burkina Faso and Cote d'Ivoire.

`add_culture.py` now runs the library image judge after loading either picks or
a fresh scrape. After media enrichment it also judges new Commons photos.
Luba followed that route: 75 candidate judgments and source records were
reviewed against contact sheets, leaving 45 objects in eight categories.
Ambiguous multi-people and question-marked British Museum attributions, a fake,
and repetitive pieces were excluded. A misclassified wooden cup was moved out
of ceramics. Six Commons photos passed a separate image-and-caption check;
unrelated music links were removed. The profile was rewritten from museum
sources, including the distinct Kiluba and Tshiluba language areas.
Fante followed the same route: 51 Codex judgments produced 35 initial picks.
The contact sheets and full catalogue check left 27 objects. One British Museum
item was actually attributed to Assin, and a Europeana drum said Fante or Asante;
both were excluded. The picker now checks the British Museum production group
against the requested people. The profile was rewritten from museum sources;
four Commons flags passed the separate image-and-caption check.
Idoma followed with 22 Codex candidate judgments, 20 initial picks and 16 after an image-sheet and 21-record British Museum attribution review. Four repetitive or weak objects were excluded. Five Commons photos passed the separate image-and-caption check; ambiguous Idoma/Igbo and Igala/Idoma attributions, digital art and unrelated items did not. The short and long profiles were rewritten around object records and the Smithsonian's caution about uncertain mask use; six unrelated Folkways links were removed.
Urhobo followed with 36 Codex candidate judgments. A 33-image contact sheet and 36 full British Museum records narrowed the selection to 25 objects. One apparent sculpture record was actually a coin reusing its parent figure image; another source note said only "probably Urhobo" despite a normalized Urhobo maker field. Five Commons photos passed the image-and-caption check. The profiles were rewritten from British Museum and Smithsonian records, and unrelated Folkways links were removed.
Fon followed with 53 Codex candidate judgments. Contact sheets of all initially selected images and all 46 full British Museum records narrowed the gallery to 28 objects. Three records loaded before their older attribution notes were checked were marked rejected: a stool first registered as Ashanti, a cloth attributed only as probably Fon, and a doll grouped with Fon and Yoruba forms. Four Commons photos passed a separate visual and caption review. The short and long profiles were rewritten around Met and British Museum object records.
The next Commons editorial batch checked 33 model-accepted Lao, Hmong, Iban and Toraja images against contact sheets and source captions. Twenty-four passed, nine failed. The rest of the backlog followed on 2026-09-28: the model judged the 710 unjudged photos (391 accepted), and all model positives went through the image-and-caption check. The site publishes 452 Commons photos; 250 failed the editorial check (see docs/vetting.md).
`build_index.py` refuses any library record without a boolean visual verdict,
and publishes Commons sidecar photos only when both `vetted` and
`editorial_reviewed` are `true`. The second check compares the image with its
caption and culture; broad Commons categories have supplied images of other
peoples even after a positive model verdict. `vet_images.py` caches the source
Commons images it judges under ignored `work/commons-review/` for that check,
so reviewers need not redownload them.
`python scripts/commons_editorial.py` runs that second check from cached images
or Wikimedia thumbnails, using up to twelve photos per contact sheet and
keeping each raw Codex reply in `work/commons-editorial/transcript.jsonl`.
Cloud Commons batches use `python scripts/export_vet_batch.py --name commons001 --commons`
and the fetch/judge/collect/apply route in [cloud-vetting.md](docs/cloud-vetting.md#commons-batches);
that route does not cache images locally for editorial review.
Run `python scripts/_vet_status.py`
for current library and Commons counts. All 7,678 library records have model
verdicts, but they have not all had a separate editorial image review. Selected
Armenian, Lobi, Dogon, Luba, Fante, Idoma, Urhobo and Fon objects were checked against source images and catalogues. Selected Commons photos had the independent second pass.

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
