# Vetting — the quality gate

`scripts/vet_images.py` shows each library image and Commons sidecar photo to a visual judge through the Claude Code CLI, or through the Codex subscription CLI once Claude nears its limit (`src/folk_patterns/backend.py`; no paid inference API). The shared call and prompt are in `scripts/vet_judge.py`; see also the [cloud path](cloud-vetting.md). It asks for BELONGS, ART_FORM, IMAGE and ERA, with reasoning and confidence. The two original questions:

1. **BELONGS** — does this picture belong under the ethnicity it is filed under?
2. **ART_FORM** — is the category right, and if not, what is?

It answers with a **REASON** and a **CONFIDENCE** before the verdict. Both are written onto the record (`cultural.vision_reason`, `cultural.vision_confidence`) and appended to `data/vet_transcript.jsonl`. A bare boolean cannot be argued with; when a filter decision looks wrong, the transcript is what you read.

## Why it is trusted

Measured 2026-08-28. Every number here came from reading the verdicts, not from the summary line.

| property | evidence |
|---|---|
| It genuinely reads pixels | 6-case blind test: metadata held fixed, image swapped underneath. Verdicts flip with the image, and follow the image when image and text conflict. |
| It generalises | 13/13 drops correct on an untouched random seed, catching contamination classes it was never tuned against |
| It is stable | 6 records × 3 repeat runs = identical verdicts and identical art_form 6/6 |
| It judges ethnicity finely | separates Shan from Bamar, Kusaibi from Wolof, Dakota from Chin, Khmer from Lao Isan |
| It fixes categories | 6/7 re-assignments correct (`household→jewelry`, `painting-mss→photo`, `unclassified→garment`) |
| Drop rate | **~26%**, consistent across seeds |

Across 100 sampled records with the current prompt, 26 of 27 drops were correct on full inspection.

**The blind test is the one to re-run if you ever doubt it.** Hold a record's metadata constant, point it at an unrelated image, and check the verdict moves. A vetter that scores well on metadata alone is not vetting.

## Scope — what the collection keeps

The collection is a general ethnographic one ([README](../README.md#what-the-collection-is)): everything a people made, wore, built or used that is beautiful and informative. Pattern is a facet, not the criterion.

| In | Out |
|---|---|
| Dress, textiles, jewellery, vessels, tools, weapons, instruments, furniture, household things | Pictures of the culture made by outsiders — European fine art, travel-book engravings, named European masters, colonial exhibition material |
| Masks, ritual and religious objects, including finely made temple and monastery work | Objects the museum's own record attributes to a different people |
| Vernacular and monumental buildings still standing, and their ornament | Maps, charts, catalogue cards, museum interiors, flags, logos |
| Documentary photographs of dress, craft, festivals and daily life, whatever the photographer's intent | |
| Court and elite art made within the culture — miniatures, album pages, royal lacquer, temple bronzes | Name-collision contamination (San → San Francisco, Cham → an emperor's title) |
| Archaeology of the homeland — excavated objects, grave goods, ancient-civilisation art, excavation sites — tagged `era: archaeological` | |
| Modern and machine-made things of the living culture — tagged `era: modern` | |
| | Images where the subject is a backdrop, or too small or blurred to read |

**How the prompt enforces it.** Since 2026-09-24 the prompt states this scope and returns, besides BELONGS and the category, an IMAGE judgement (the backdrop/unreadable row) and an ERA (modern items belong, and are tagged). Measured results and the model trade-off: [Scope-aligned prompt](#scope-aligned-prompt--calibration-2026-09-24). Some rule texts further down still carry the pattern-first wording of the original prompt; the rules themselves are unchanged.

## What the prompt encodes

Each of these rules exists because its absence produced a measured, specific failure. Do not remove one without re-running the calibration. The prompt states them in compressed form (`SYSTEM_PROMPT` in `scripts/vet_judge.py`, ~3k characters); this list is the reasoning behind each line — see [Short cached prompt](#short-cached-prompt--2026-09-24).

- **A European holding country is never grounds for rejection.** Europeana's location field is the *holding museum*. Treating it as origin sent Europeana's reject rate to 86% and discarded Iban pua kumbu, Batak ulos, Minangkabau songket. See [museums.md](museums.md).
- **Monumental architecture is in scope.** Mosques, temples, palaces, mausolea, forts, walled towns — including famous, imperially-patronised ones. Without this the judge invented a vernacular-vs-monumental line the atlas does not draw and dropped Hagia Sophia, Wat Phra Kaew, Bibi Khanym and Khulbuk.
- **Photographic style is never a reason to reject.** Staged, modern, touristic, charity or news photographs still document the culture if the subject shows traditional dress, craft or life. Judge the subject, not the photographer's intent. Only reject when the actual subject is something else — a street market where a monument is mere backdrop.
- **Religious art made by the culture counts** regardless of how finely made. Ethiopian Orthodox painting on hand-woven cotton, Buddha figures, mosque tilework. Judge who made it, not what it depicts.
- **Ethnicity tie-break.** If the museum's own record *names* a different people, it is mis-filed → NO. If the group is merely unverifiable, keep it — we cannot tell neighbouring groups apart by eye either, and absence of proof is not evidence of a mistake.
- **Archaeology is in, tagged by era.** Excavated objects and the art of ancient civilisations of the homeland are YES with `era: archaeological`, so the site can show them as their own section. Earlier prompts rejected them.
- **Out of scope:** European fine art including named masters documenting the culture (Rubens' costume book), colonial exhibition material, maps and charts, photographs of modern named politicians or celebrities, museum catalogue cards.

Tuning history, all measured on identical records: drop rate **44% → 28% → 26%**, each reduction traceable to removing one named over-strictness.

## Running it

```bash
# calibration — verdicts printed, nothing written
python scripts/vet_images.py --target library --limit 50 --seed 7777 --dry-run

# a real chunk, persisted
python scripts/vet_images.py --target library --limit 500

# one museum only, or one ethnicity
python scripts/vet_images.py --target library --source british_museum
python scripts/vet_images.py --target library --only uzbek
```

Keep `--workers` at the default 3: Sonnet answers `Server is temporarily limiting requests (not your usage limit)` above that (measured 2026-09-24, 10 workers failed after 18 calls). Sonnet throughput on the full library is not yet measured; the ~50 records/minute figure was Haiku at `--workers 20`. All records go through one global thread pool; an earlier per-file pool put a barrier between `metadata.json` files and pinned throughput at 14/min regardless of worker count.

**Failure mode to watch: quota exhaustion.** When the CLI starts failing, every call returns non-zero and the script records `vision_vetted: None` while the progress counter keeps climbing — a run can look healthy and produce nothing. On 2026-08-27 this happened after ~650 records and the remaining 3,338 were logged as errors. Watch the *verdicts*, not the counter. Resume is safe: records are skipped only on a real boolean verdict, so `None` records are retried automatically.

`build_index.py` consumes the results — it drops records whose `vision_vetted` is `False` and prefers `art_form_vision` over the rule-based classifier. `build_index.py` now refuses to publish if any library record has a missing or non-boolean verdict. Commons sidecar photos appear only when `vetted` is `true`; download or CLI failures remain hidden until retried.

## Where this stands

Run `python scripts/_vet_status.py` for live numbers — it reads the library, so it is never stale. Snapshot 2026-09-24, after the British Museum facet re-scrape:

| source | records | kept | dropped |
|---|---:|---:|---:|
| british_museum | 2,043 | 1,575 | 468 |
| commons_arch | 1,295 | 1,162 | 133 |
| europeana | 866 | 642 | 224 |
| cleveland | 559 | 543 | 16 |
| va | 466 | 429 | 37 |
| met | 69 | 66 | 3 |
| smithsonian | 33 | 32 | 1 |
| rijks | 15 | 8 | 7 |
| **all** | **5,346** | **4,457** | **889 (17%)** |

Every record carries a current-prompt verdict with its reasoning; nothing is left to judge. The 721 British Museum records scraped through the "Ethnic group" facet ([museums.md](museums.md#british-museum)) were judged 702 kept / 19 dropped (2.6%), against 34% for the keyword-scraped BM records:

| culture | kept | dropped | | culture | kept | dropped |
|---|---:|---:|---|---|---:|---:|
| San | 59 | 1 | | Iban | 55 | 0 |
| Igbo | 59 | 1 | | Hmong | 57 | 1 |
| Chin | 59 | 1 | | Toraja | 41 | 3 |
| Kikuyu | 56 | 4 | | Ndebele | 58 | 1 |
| Sotho | 60 | 0 | | Fang | 55 | 1 |
| Turkmen (+1 Afghan Turkmen) | 61 | 0 | | Uzbek | 24 | 4 |
| Maasai | 58 | 2 | | | | |

## First persisted chunk — 2026-09-24

`--target library --force --limit 200 --seed 20260924 --workers 10`, run locally on the subscription. Writes landed: `_vet_status.py` counted 200 verdicts carrying reasoning afterwards. 2 of 200 came back `(cli exit 1)` on V&A records whose images are intact on disk; they stay `None` and are retried by the next run.

| source | kept | dropped | drop % |
|---|---:|---:|---:|
| british_museum | 32 | 26 | 45% |
| europeana | 17 | 13 | 43% |
| commons_arch | 43 | 16 | 27% |
| cleveland | 20 | 5 | 20% |
| va | 18 | 0 | 0% |
| met / rijks / smithsonian | 6 | 2 | — |
| **total** | **136** | **62** | **31%** |

**Drops (all 62 reasons read, 6 images opened): correct.** The high British Museum and Europeana rates are real contamination, not over-strictness — ethnonym collisions ("San" → San Francisco beach and zoo photos, Naples; "Cham" → Nieuhof's 1669 China etchings; "Fang" → a George III satire print and a Shakespeare engraving; "Chin" → a drawing after Perugino), a Stockholm catalogue card, and records the museum itself attributes to another people (Ewe kente under Ashanti, Dayak darts under Javanese). One Europeana record (`europeana-2020903_KMS7`, "Don Miguel de Castro, Emissary of Kongo") carries an unrelated image of a glass-and-water installation — the source served the wrong file.

**Keeps (30 random, judged from the images on contact sheets): 2 wrong, 4 weak, 24 good.**
- Wrong: a machine-woven kente imitation kept under Ashanti although its own reason says it imitates kente; a modern 20th-century Malaysian mosque interior.
- Weak — belong, but make a poor image of the culture: a Tunis rooftop shot through a blue grille with the mosque as backdrop, a distant El Badi palace postcard, a recent painted church filed as Debre Damo. An Angkorian bronze figure was also flagged at the time; under the [scope](#scope--what-the-collection-keeps) it is court and temple art made within the culture, so it is in.

The prompt judges *belonging*, never *image value*, so the weak class passes by design — that is the gap named in the scope section.

## Scope-aligned prompt — calibration 2026-09-24

The prompt now carries the ethnographic scope, the category list below, and two new judgements written onto every record: `vision_image` (good / weak / unusable) and `vision_era` (traditional / modern).

Categories (`art_form_vision`): textile, garment, jewelry (shown as adornment), ceramic, metalwork, **arms**, **masks-ritual**, sculpture, **instruments**, household, architectural, painting-mss, photo, unclassified. Slugs stay stable because `classify.py`, the library folders and the site share them. **Architectural is buildings and parts of buildings only** (panels, boards, lintels, doors, tiles) and photos of buildings; a model or miniature of a building, and a carved figure, head or mask taken from a building (Māori house-post figure, tekoteko, koruru), is sculpture. On 2026-09-26 that moved 11 models (Ainu, Inuit, Haida, Chin, Thai Wat Phra Si Sanphet, a Topkapı room) out of architectural and dropped a 3D computer rendering of the Kairouan mosque; the same day 5 Māori house and gable figures moved to sculpture. Then `vet_images.py --recheck-art-form architectural` re-judged all 1,028 architectural records under the rule, changing only the category (BELONGS/IMAGE/ERA stay final): 38 moved (23 to photo — people in front of a building —, 7 sculpture, 5 painting-mss, 1 each instruments, arms, household); one wrong move, a mausoleum interior, was set back by hand. The move and its reason sit in `cultural.art_form_recheck`.

Calibrated on 60 records labelled by eye first — the 30 keeps above, 15 drops from the first chunk, 15 never judged — then run dry (`$TEMP` harness calling `_vet_library_record`, nothing persisted):

| | Haiku 4.5 | Sonnet |
|---|---|---|
| BELONGS, decisive cases (~53) | 1 wrong | 1 wrong (the same one) |
| Court art — Shahnama folio dropped by the old prompt | flipped to YES | YES |
| IMAGE weak, 3 by-eye cases | **0 / 3** — calls everything good | **2 / 3** |
| IMAGE unusable, 3 cases | 3 / 3 | 2 / 3 (the third was dropped on BELONGS anyway) |
| ERA modern, 4 by-eye cases | 3 / 4 | 3 / 4 |
| Throughput | 10 parallel workers, no errors | server rate limit after 18 calls at 10 workers ("temporarily limiting requests (not your usage limit)"); 3 workers ran clean |

- The one BELONGS error on both models was a Pharaonic Book of the Dead kept although the prompt then excluded antiquity. Archaeology is now in scope with its own ERA, and a Sonnet re-check of six records came back right on all six: Ban Chiang jar, Book of the Dead and Jiaohe → YES + archaeological; Shahnama → traditional; kanga → modern; the Tunis grille shot → weak.
- One of my own by-eye labels was wrong: V&A's record names a `_regional` ikat as Shan, so the drop both models gave it was correct.
- Haiku reasons its way past weak images ("the grille does not obscure the architecture"). That is why `MODEL` is Sonnet.

## Fresh 50-record review — 2026-09-24

50 random records (seed 424242, none from the calibration set), Sonnet, dry run, every image then judged by eye on contact sheets with the verdict printed under it.

| | result |
|---|---|
| BELONGS | 48 right, 1 wrong, 1 debatable. Wrong: a purple figurative silk ikat with elephants and pavilions filed under Uzbek, kept by the "unverifiable → keep" tie-break although its style is Cambodian *pidan*. Debatable: a studio portrait of a man in a Western jacket filed under Berber, dropped. |
| drops | all 15 real: Laozi scroll filed under Lao, Japanese scroll under Kongo, European academic drawings under San and Chin, a book cover, a catalogue card, an Italian state dinner under Somali, Solomon Islands sheep under Javanese |
| IMAGE | nothing weak passed as good; one weak verdict (a jar-stopper sealing) fair |
| ERA | kangas, a Sotho factory blanket, adire correctly modern; two near-identical stencilled kanga samples split modern / traditional |
| category | 2 misses — an nkisi power figure → sculpture, a vajra ritual bell → metalwork; both should be masks-ritual |

Changes made from it, then re-run on the 12 affected records and on the 30 calibration keeps as a regression check:

- **masks-ritual** now lists power figures, ritual bells and implements, amulets, reliquaries, altars and offering vessels explicitly; statues of deities stay under sculpture. Both misses moved to masks-ritual.
- **Distant-style tie-break:** an unmistakable signature style of another world region (pidan under Uzbek, Chinese ink scroll under Lao) → NO, explicitly never applied to neighbouring groups. The pidan flipped to NO; all 29 calibration keeps stayed YES.
- Repeat runs are not fully deterministic on IMAGE and ERA: in the regression one clean Turkmen carpet photograph came back weak, and an Angkorian bronze and a My Son stela moved to archaeological (defensible).

**Operational findings**
- Sonnet rate-limits in bursts even at 3 workers — one run had 43 of 50 calls fail with `Server is temporarily limiting requests (not your usage limit)`. `_ask_claude` now backs off 30 → 60 → 120 → 240 → 300 s and retries; on the retry run 42 of 43 went through, the last one a timeout.
- Single Sonnet calls can exceed 90 s; the per-call timeout is 180 s.
- **Throughput: ~5 records/minute** at 3 workers (20 records in 238 s, 43 in 522 s). The full 4,625-record `--force` pass is therefore on the order of 15 hours of wall time.
- 128 Europeana records point at thumbnails of PDFs (`&type=TEXT`): general catalogues, catalogue cards, book scans, but also 11 Balinese manuscripts. They are left to the vetter rather than filtered by URL, because the manuscripts are real.

## Short cached prompt — 2026-09-24

The rules were first a ~10k-character prompt sent in the user message with the record block inside it. They are now a ~3k-character system prompt, identical on every call, with the record block and the image as the user message. Two reasons, both measured on the 117 pilot records (docs/cloud-vetting.md → Pilot 3):

- **Cost.** A fixed system prompt is served from the CLI's prompt cache after the first call: $0.035 → $0.011 per record. Moving the long rules into the system prompt alone gave $0.0116; shortening them adds only ~7%, because the image and record block, written fresh every call, are most of what is left.
- **No loss in judgement.** The current prompt against the long prompt's cloud pilot: BELONGS 113 / 117, and on the 89 records both keep ART_FORM 84, IMAGE 88, ERA 85. Against local Sonnet with the long prompt: BELONGS 112 / 116. Against the by-eye labels: 55 / 60 (the long prompt: 55–56). Three of the five misses are wrong labels — the Book of the Dead and the Ban Chiang jar were labelled under the old rule that excluded excavated material, and V&A's record names the "Bamar" ikat as Shan — which leaves an Ewe kente kept under Ashanti and a printed cloth whose museum record calls it adire dropped, both borderline.

Getting the short prompt there took measured rounds on the 117 pilot records and on batch b002's drops, each fixing a class the previous one missed — keep these lines:

- First cut: ERA 81 / 89. Khmer and Javanese temple bronzes came back `traditional`. Fixed by naming "an Angkor-era or 10th-century temple bronze or ritual bell now in a museum" as archaeological, "even when it is well made and court or temple art".
- That pushed an Ilkhanid Shahnama folio to `archaeological`. Fixed by "a painting or manuscript of a living tradition (a Shahnama folio, a Mughal album page) is traditional, however old; one recovered from a tomb (a Book of the Dead) is archaeological".
- A sarong/longyi as `textile` and ritual bells as `metalwork`: the category line names them.
- In batch b002 three pictures were dropped only because the record's claimed category was wrong — the Hanging Church and the Tash Rabat caravanserai filed as `textile`, an Iranian façade filed as `painting-mss` — and a Khmer temple in Isan was dropped under Lao Isan. The long prompt asked "is the category right, and if not, what is?"; the short one had lost that. Fixed by "a wrong claimed category alone is never a reason for NO — give the right ART_FORM" and "archaeology of their homeland, even when an earlier people built it (a Khmer temple in Isan)". All four came back YES.
- The first wording of that category line ("never a reason for NO") made the judge lenient: a scanned document page about kilims and the Cambodian pidan filed under Uzbek flipped to YES. Fixed by adding "every NO reason above still applies", "a scanned page of a document or book" to the NO list, and "a pictorial Cambodian pidan silk under Uzbek" to the distant-style example — the long prompt had named that exact case. The pidan went from YES on 3 / 3 repeat calls to NO on 3 / 3.
- A single re-run is noisy: a kitten in the Kasbah of the Udayas flipped to YES once, then came back NO on 3 / 3 repeats. Check a flip with repeats before changing the prompt for it.

The remaining ERA differences from the long prompt are ones the short prompt gets right or that are debatable: Masjid Shah Alam (built 1988) → modern, the Dungur ruins → archaeological, a kanga design proof and a Lao checked silk → modern.

Verdicts written by the long prompt (the pilot and batch b001) count as current and are not re-run. Batch b002 was judged before the category fix: its 38 drops were re-judged with the current prompt (`data/vet_verdicts/b002-drops-rejudged.jsonl`, applied after `b002.jsonl`; 7 flipped to YES), its keeps stand, since that fix only makes the judge keep more.

## Re-attributing drops

The vetter answers one question — does this object belong to the culture it is filed under — so a good object filed under the wrong people is a NO: a Shan cloth under Bamar, New Gourna mosque under Nubian, a Batak wedding jacket under Minangkabau. `scripts/reattribute_drops.py` recovers those.

```bash
python scripts/reattribute_drops.py propose   # text: who made it? which atlas culture is that?
python scripts/reattribute_drops.py rejudge   # image: the normal judge, claiming that culture
python scripts/reattribute_drops.py apply     # cultural.reattribution on the library record
python scripts/reattribute_drops.py report    # counts, incl. peoples the atlas lacks
```

1. **propose** — one `claude --print` per 40 drops, text only: the museum metadata plus the vetter's own reason. It names the people who made the object (or none: a specimen, a scan, a San Francisco postcard) and the atlas culture that *is* that people (or none). When the model names a people that is exactly an atlas culture's name but leaves the key empty (it did for two Batak jackets), the script fills it in.
2. **rejudge** — every proposal with an atlas culture goes through `vet_judge.judge` with the image, now claiming that culture. Only a YES moves the record.
3. **apply** writes `cultural.reattribution {to, people, belongs, art_form, image, era, reason}`; `build_index.py` files a dropped record with `belongs: true` under `to`, with the re-judge's category, image and era verdicts.

Outputs live in `data/reattribution/` (`proposals.jsonl`, `verdicts.jsonl`, and every raw reply in `raw.jsonl`).

**Run of 2026-09-24, all 868 drops:** propose $8.30, rejudge $0.98 (CLI-reported). 88 proposals named an atlas culture; the re-judge said YES to 79 and NO to 9. By eye, 17 of 20 random YES moves are clearly right (an Egyptian coffin from a Khmer search, Javanese batik under Minangkabau, a Kyrgyz shyrdak under Kazakh (Xinjiang), Batak jackets, Persian album paintings under Chin, New Gourna mosque) and 3 are defensible but soft (a Cairo costume-album leaf, a photo filed as Khulbuk that the judge reads as Khiva, a songket called Malay as "plausible").

Most drops are not misfiles: 283 name no people at all, and of the rest the largest groups are outsiders (Italian 74, English 69, Dutch 51, Japanese 45, Indian 31, Chinese 22). **Peoples the atlas has no culture for** — the list to read before adding one: Armenian 15, Shan 13, Hausa 7, Ewe 5, Sumba 3 (`report` prints it).

## Next steps

### Codex CLI subscription fallback (2026-09-27)

`src/folk_patterns/backend.py` switches to Codex by itself when Claude's
subscription usage passes 90% (`FOLK_CLAUDE_MAX_PCT`) of the 5-hour or weekly
limit, or a Claude reply says the limit is hit; the same record is then
retried through Codex. Verified 2026-09-28: one Tlingit photo judged by Codex
(12 s) with `FOLK_CLAUDE_MAX_PCT=1`, and by Claude (4 s) at the default, each
labelled with its own model. `FOLK_LLM_BACKEND=codex` or `=claude` forces one.
The Codex branch sends the same judge rules, record metadata and image reduced
to 1024 pixels via `codex exec -i`, using `gpt-5.6-luna` at low reasoning effort.
Each call runs in an empty temporary directory, without user MCP configuration,
and no paid inference API. The raw pick cache is shared: existing Claude
verdicts are reused, while new rows and library records identify the judge.
The short writeup branch uses the same JSON shape and the existing audit.

On three previously judged Europeana images, Codex matched Claude's BELONGS
and IMAGE verdicts (3/3). It matched the basket's QUALITY 4, but rated plain
recycled-tire sandals 3 instead of 2 and dye bark 2 instead of 1. Review new
Codex QUALITY 3 records by eye before onboarding. This is a small calibration,
so it does not establish equivalence over other categories or peoples. The
responses are saved locally in ignored `work/codex-vet-calibration.jsonl`.

On Aymara and Tibetan, 24 previously uncached candidates were judged through
Codex. After visually checking the accepted new picks and the source records,
five were excluded: a plain bowl and miniature bricks rated QUALITY 3, a
Tibetan caravan photograph whose image URL returned 404, and two Met records
with the same armor-installation image misclassified as a photograph and a
sculpture. The exclusions are recorded in
`data/world/pick_exclusions.json` so future pick runs retain the corrections.

For Pende, Codex judged 90 candidates and the first pick retained 87. Contact
sheet review excluded 11 more: plain or worn pieces with little visible craft
detail, plus a photographic print of a Pende mask that the judge mistook for
the mask. The final pick has 76 records across nine categories; 75 appear in
the deduplicated site index. The Pende media sidecar also needed source review:
the Commons plural category `Pendes` returned photographs of a Spanish village,
and generic `wood carving` brought in unrelated work. Both categories are
skipped for Pende. Country-level Smithsonian Folkways results are withheld
unless their titles identify Pende; the current sidecar has no such result.

For Asmat, Claude judged 145 candidates across four pick runs (British Museum
and Europeana). Review of every kept image and all catalogue descriptions
excluded 41 objects, leaving 89:

- 17 British Museum photographic prints (`EA_Oc-B142-*`, `EA_Oc-B101-*`)
  showed an object. The judge re-filed each print as that object's category,
  the same mistake as the Pende mask. `pick` now drops any record titled
  "photographic print", "photograph" or "postcard" whose judged category is
  not `photo`. Postcards came in with Tlingit, whose BM totem-pole postcards
  were filed as sculpture. Prints of people and ceremonies stay.
- A figurative painted mat from the British Museum's 2009 Stanley accession
  was made in 2005, so it is contemporary art rather than a traditional object.
  So is a second mat from the same accession. Plaited bags from that
  accession, dated 2002, stay as handmade craft.
- A Europeana carved crucifix is a mission-period Christian subject.
- The rest were thin items that cannot be made out at gallery size, plain
  pieces and near-repeats of bowls, drums, trumpets and skirts.

The British Museum's own "uncertain" production-group flag removed three
more.

The generated Asmat profile invented vernacular terms that no source
contains: *otsj* (shield), *wuramon* (soul ship), *bipane*, *ambirak*, *yew*,
*em*/*tifa*, *fu*, *cus*, *tsjemen* and the culture hero *Fumeripits*. It also
called the *jew* a men's house, although Wikipedia gives *jew* as the word for
dwellings. Both profiles were rewritten from the Wikipedia articles (Asmat
people, Bisj pole) and the museum records only. *Cemen* (the pole's openwork
"wing"), *jipae*, *ci*, *wow-ipits* and *Safan* are sourced. The seed's
tradition chips carried the same invented terms and were cut to sourced ones.
Of the 12 Asmat Commons photos, the model rejected one (a betel stall). The
editorial pass published eight. It rejected a Jakarta parade captioned only as
"friends of Asmat", a skull attributed to "Asmat-Mimika", and a cropped
duplicate of the UBC shield photo.

For Tlingit, the first pick kept 21 of 31 judged. The British Museum's own
flags removed 64 records ("multiple peoples" or "uncertain"; Northwest Coast
objects are often catalogued as "Tlingit or Haida"), and each used one of
the category's ten tries without a judge call. Source-attribution drops no
longer count as tries. The rerun reached 52 judged and kept 36, and review
excluded seven: a near-repeat Met lute, a Met whistle attributed "Tlingit or
Koluschan, probably", plain reed pipes, a Sitka National Monument tourist
postcard, a curio-shop display, a plain spoon knife and a plain copper sheet.
One replacement, a 1930s postcard of a Wrangell potlatch procession, was kept:
30 objects, $0.60 in all. The generated profile again used Tlingit terms
absent from every source (*shakee.át*, *kooteeyaa*, *gaaw*, *kéet*, *xóots*,
*s'áaxw*, *tináa*, the Whale House), and called Chilkat and Ravenstail
weaving unique to the Tlingit, whereas Wikipedia says they are shared with the
Haida and Tsimshian and that Ravenstail began among the Tsimshian. Both profiles
were rewritten from the Wikipedia articles (Tlingit, Culture of the Tlingit,
Chilkat weaving, Ravenstail weaving, Formline art) and the museum records.
The Commons pass published four of ten model positives. It rejected a Chilkat
blanket whose caption names only Fort Rupert, a museum mural by Will S. Taylor,
a drawing of "Tsimshian, Haida, and Tlingit" chiefs' costume, two unattributed
report plates and a blanket captioned Tsimshian.

`generate_writeups.py` now shows the writer only its sources (Wikipedia plus
related articles, UNESCO ICH, museum catalogue text), audits italic terms and
numbers against them as whole words, and retries once. Rerun on Tlingit with six
articles and 30 museum records: one unsupported term in the first draft
(*naaxein*, true but only in the Chilkat weaving article, which is not fetched),
none after the retry. The published Tlingit profile stays the hand-checked one.
Substring matching had let *tifa* pass on "artifact", *hit* on "white" and
*otsj* on "Otsjanep".

The audit folds text before it splits words. A combining mark had split
*nuučaan̓uł* in two, and "4,000" failed against a source reading "4000". The
Hawaiian ʻokina is dropped, because the museum records write *kupee niho ilio*,
*ahu'ula* and *ukeke*. Before that, five true Hawaiian terms were flagged and
`--fix` stripped their italics from the short profile. The short rewrite can
also italicise a word the long draft used plainly (Hopi *manta*), so
`add_culture.py` runs `audit_profile.py --fix` after it.

### Thin cultures: Cleveland, V&A and Met do not fill them (2026-09-28)

`data/pool/assigned.jsonl` holds thousands of Met, Cleveland and V&A rows for
the thin cultures, but they are country matches (`status: candidate`), not
attributions. A random 20 per culture (140 rows) named the people in none: V&A
"Iran" bowls from 1180-1220, Bukhara tiles from 1358, 1725 Vietnamese export
saucers, Bactrian coins filed under Hazara. The museums' own full-text search
(V&A and Cleveland APIs) and the Met open-access CSV were then searched for each
name:

| Culture | Country-matched pool rows | Records naming the people |
|---|---|---|
| Qashqai | 5,990 | V&A 7 carpets (unopened); Met 0; Cleveland 0 |
| Yakan | 205 | Cleveland seputangan headcloth; Met jungle knife 31158 |
| Karakalpak | 200 | V&A 1 saddle bag |
| Oromo | 130 | Met 2 headrests (not public domain); V&A 1 necklace |
| Sidama | 130 | Met 3 headrests "Sidaama peoples (?)", not public domain |
| Cham | 439 | only Champa-kingdom sculpture (Met 4, Cleveland 3) |
| Hazara | 221 | V&A's 34 hits are Hazara district, Pakistan (phulkari) |
| Afar, Pamiri | – | 0 |

The Met search API (`/public/collection/v1/search?q=...&hasImages=true`)
returned the same list (Book of the Dead of Imhotep, the Fieschi Morgan
Staurotheke, ...) for every query tried, so its totals are not a count of
matches; use `.cache/MetObjects.csv` instead.

### Eight cultures from picks (2026-09-28)

| Culture | Kept by pick | Published | Commons | Excluded by review |
|---|---|---|---|---|
| Hopi | 47 | 44 | 9 of 11 | Snake Dance photo and painting (editorial call, not in the sources) |
| Shona | 62 | 60 | 3 of 3 | near-repeat divination tablets |
| Nuu-chah-nulth | 41 | 41 | 2 of 2 | none |
| Ibibio | 77 | 77 | 3 of 6 | Efik board, Igbo doors, modern Nsibidi scroll (Commons) |
| Tetela | 60 | 60 | 1 of 1 | none |
| Nupe | 78 | 78 | 6 of 10 | "Hausa oder Nupe" mask and unattributed photos (Commons) |
| Torres Strait Islanders | 81 | 80 | 2 of 3 | second photo of one bottle |
| Native Hawaiians | 83 | 81 | 6 of 11 | two royal portraits in Western dress; lauhala "German stars" (Commons) |

Every seed's tradition chips were pruned to the ones the sources contain.
Between 11 and 16 were dropped per culture, for example 16 Shona chips such as
*hozi* and *chikuva*. The Shona load first wrote 0 records: every British Museum
fetch got 403 while two picks shared the Chrome. `_load_picks.py` now re-reads
the cookies when a British Museum record comes back empty.

The Commons backlog review rejected, most often: monuments of ancient states
filed under a living people (Persepolis, Narmer, Tiwanaku, Kerma); generic
country photos repeated across cultures (the same Ethiopian headrests, coffee
ceremony and gameboard under both Oromo and Sidama; "umembeso" under Ndebele,
Sotho and Xhosa); festival and agricultural-show crowds with no attribution
(Bemba, Bwa, Boya); and modern portraits of officials. Oromo lost 9 of 11 and
Sidama 10 of 12 this way.

For Hausa, Codex judged 123 world-list candidates and initially retained 104.
Review of contact sheets and original catalogue descriptions excluded 39: plain
or near-duplicate pieces, faint Qur'an boards, damaged toys, a Ghanaian
goldweight described only as *derived from* a Hausa form, and two pottery
photographs that the British Museum places in Kosti, Sudan without a Hausa
attribution. The final pick and site shard contain 65 objects across 13
categories. The generated writeup's population estimate and claim that no
Nigerian UNESCO inscription existed were removed; both the long and short
versions were rewritten from selected object records, Smithsonian catalogue
entries and UNESCO's 2024 Durbar in Kano description. The media sidecar has
nine reviewed Commons photos, one relevant UNESCO entry and no Folkways
results: country-level Folkways hits and ambiguous Commons categories such as
`hula` had supplied unrelated material.

The full re-vet is done: on 2026-09-24 every one of the 4,625 records then in the library carried a verdict from the current prompt — 3,755 kept, 870 dropped (19%); IMAGE good 4,428 / weak 141 / unusable 56; ERA traditional 3,556 / modern 579 / archaeological 490. The run: [cloud-vetting.md](cloud-vetting.md#full-run--2026-09-24). `build_index.py` treats the verdict as final for every source, and drops that belong to another culture are re-filed (above).

For Shan, Codex judged the missing candidates through the subscription CLI.
Two image and catalogue review rounds reduced the machine selection to 34
objects across ten categories, with 82 Shan exclusions recorded for future
pick runs. Rejected records include watercolor album leaves depicting several
groups in Shan State without a Shan maker, objects attributed to Kachin or
Bulang makers, and Taungyo/Taungthu pieces whose descriptions say only that
Shan people also used them. The retained tattoo design stamp is explicitly
attributed to a Shan maker; its Burmese owner is a separate catalogue fact.
The long and short texts were rewritten from selected records and British
Museum and Library of Congress sources. Broad Shan State and Myanmar Commons
categories and generic Folkways results were removed from the media sidecar.
The 34 images were uploaded to R2; the local index contains 6,387 objects and
101 cultures. Subscription CLI inference cost was $0.00 in API charges.

For Konyak, the Codex subscription CLI judged 53 initial British Museum
candidates in 438 seconds, with no paid API charge. Contact sheets and the
museum's complete catalogue descriptions were checked for every one of the 52
initial keeps. Ten exclusions cover a helmet also attributed to Tangkhul, a
questionable panji holder whose darts were made by an Assamese craftsperson,
a plain spindle and belt, a toy, a duplicate lime-box lid, a duplicate comb,
a weak helmet view, a thin tassel and part-only roof-ornament birds. Rerunning
the cached pick filled three gaps and left 45 selected objects in eight categories.
The roof-ornament upright is a *model* and was manually reclassified from
architecture to sculpture through `data/world/pick_overrides.json`, so a
future pick rerun preserves the correction. Two retained nineteenth-century
pieces are marked `Konyak (?)` by the museum, but the acquisition notes call
them Jabboka Naga objects and Nagaland's Mon district plan lists Jaboka among
Konyak chiefly villages. Their uncertainty is noted in the profile. The
generated seed, long and short profiles, and media sidecar were corrected
against British Museum, Bowers Museum and Nagaland sources. Generic India
Commons and Smithsonian Folkways results were removed. The local index now
contains 6,432 objects and 102 cultures; all 45 Konyak images are on R2.

For Armenian, contact sheets and V&A catalogue records were reviewed for 46
photographed Armenia-place candidates. Nineteen varied objects were retained
through a curated pick overlay, alongside nine earlier world picks. The Codex
subscription judged all 19 selected V&A images; three additional judged-yes
items were manually excluded as weak or visually obstructed. The generated
Commons media set had twelve unrelated or generic images and was removed.
UNESCO's official Armenia page lists eight entries that Wikidata's country
query missed; all eight are supplied by the curated supplement. The generated
profiles were replaced with source-grounded short and long versions. All 28
Armenian library records have visual verdicts. Four Europeana photographs load
at 400 pixels because their full-size endpoints returned 401.

For Lobi, the Codex subscription judged 31 British Museum and Cleveland
world-list candidates. Contact sheets and source records narrowed the set to
16. Thirteen weak, duplicate, or part-only images were excluded; a further
figure was removed because the British Museum also attributes it to Lo Willi
and Dagari. The profile and seed were corrected for the cross-border
distribution and for the linguistic difference between Lobiri and Birifor.
The Palmer Museum identifies `bateba` as figures associated with living
guardian spirits, so the generated claim that they portray ancestors was
removed. Of 12 Commons photos accepted by the image judge, eight passed
independent image-and-caption review and four were rejected as obscured or
repetitive. Four general Ghana or Ashanti Smithsonian music links were also
removed. All 16 library images are on R2.

For Dogon, the Codex subscription judged 88 world-list candidates and kept 83.
All 83 images and museum records were inspected; 13 were excluded before loading,
including a Met pendant attributed to Dogon **or Bozo**, a Cleveland ring marked
only *Dogon-style*, a separate lock component, and weak or repetitive objects.
The remaining 70 records were loaded with their verdicts. Two Europeana image
endpoints served text; their cached thumbnails loaded successfully. The profile
was rewritten against UNESCO, Met, Smithsonian, British Museum and linguistic
sources; the seed no longer treats Tellem or bogolan as Dogon traditions. Of 12
model-accepted Commons images, eight passed separate image-and-caption review.
A Met sculpture actually attributed to a Soninke blacksmith and a Brooklyn
Museum piece attributed to Dogon **or Tellem** were rejected, as were two
photographs without a specific Dogon attribution. Both generated Folkways
links were unrelated and removed. All 70 library images are on R2.

For Luba, the Codex subscription judged 75 world-list candidates. A contact
sheet review of all 67 initial positives, followed by a full museum attribution
audit, left 45 objects in eight categories. The second pass excluded ambiguous
Luba/Songye and other multi-people records, Europeana's "probably Baluba or
neighbors" records, a British Museum fake, eight British Museum records whose
production ethnic group was explicitly marked `(?)`, and duplicates. A cup
judged ceramic was described by the museum as wood and moved to household.
The profile was rewritten from Met and Brooklyn Museum object accounts. Six
Commons photos passed independent image-and-caption review; four unrelated sea
photos and two Brooklyn objects lacking a specific Luba attribution were
removed. All six generated Folkways links were unrelated and removed. The 45
library images are on R2.

For Fante, the Codex subscription judged 51 world-list candidates and
initially kept 35. A contact-sheet and full catalogue review narrowed this
to 27 objects in seven categories. The British Museum attributed a kept
*kuduo* to Assin, while a Europeana drum said Fante or Asante; both were
excluded. Two portraits lacked a specific Fante identification, and weaker or
repetitive objects were removed. Four Commons flags passed independent image
and source checks; the food photo and duplicate flags did not. Four generic or
unrelated Smithsonian links were removed. Both profiles were rewritten from
museum sources and all 27 object images were uploaded to R2.

For Idoma, 22 Codex candidate judgments yielded 20 initial picks. Contact sheets
and 21 full British Museum records confirmed explicit Idoma maker attributions;
four visually weak or repetitive items were removed. The final gallery has 16
objects. Five of 11 Commons photos passed a separate image-and-caption review;
items labeled Idoma or Igbo, Igala or Idoma, a digital illustration, and
unrelated content were withheld. Six generic or unrelated music links were
removed. Both writeups were replaced with claims grounded in museum object
records and the Smithsonian's caution that mask uses can be uncertain.
All 16 object images are on R2.

For Urhobo, the Codex subscription judged 36 world-list candidates and
initially kept 33. A three-sheet visual pass and 36 full British Museum records
left 25 published objects. The judge mistook a Liberian coin for its parent
figure because the record reused the sculpture photograph. A shrine was
rejected after loading because its donor note said only "probably Urhobo,"
despite an unqualified normalized maker field; its library verdict records
this correction. Five of 12 Commons photos passed an independent visual and
caption check. Both profiles were rewritten from British Museum and Smithsonian
object records, and generic music links were removed. All 26 loaded images
were uploaded to R2; the rejected shrine is excluded from the index.

**Current coverage (2026-09-28):** `scripts/_vet_status.py` reports 8,318/8,318
library records judged, 7,420 accepted and 898 dropped. The site index has
7,322 objects in 120 cultures. This is model-based visual review of all library
images, not a separate editorial check of 8,318 images. Selected Armenian,
Lobi, Dogon, Luba, Fante, Idoma, Urhobo, Fon, Asmat, Tlingit, Hopi, Shona,
Nuu-chah-nulth, Ibibio, Tetela, Nupe, Torres Strait Islander and Native Hawaiian
images were also checked against contact sheets and catalogues. Commons: 452
published, 250 rejected by the editorial check, 666 rejected by the model, none
waiting.
The index excludes two images marked unusable. A weak
image may still show a useful object, so it is not automatically rejected.

Commons has two gates: the subscription CLI image judge sets `vetted`; a
separate image-and-caption review sets `editorial_reviewed` on accepted photos.
The index publishes only images passing both. The five initial test cultures
(Navajo, Gbaya, Igorot, Toba and Guna) have had the second pass. It rejected 20
model-accepted but wrong Commons photos: celebrities, unrelated peoples or
places, generic scenes, and colonial exhibition portraits. Twenty-five additional
legacy positives were checked; seventeen were rejected, including photos from
the wrong country in the atlas's split cultures. `scripts/_vet_status.py`
reports the changing Commons counts, including photos waiting for either gate.
The five Codex batches now record `vetted_by: codex-gpt-5.6-luna`; the old
constant had incorrectly labeled their verdicts as Claude Sonnet.
The same Codex CLI and visual review covered Ainu (12 judged, three published),
Konyak (three judged, three published), Lobi (12 judged, eight published),
Dogon (12 judged, eight published), Luba (12 judged, six published), Fante (12 judged, four published), Idoma (11 judged, five published), Urhobo (12 judged, five published), and Fon (12 judged, four published).
Ainu's model positives included
two 1904 World's Fair exhibition portraits and an outsider painting; the
second pass rejected them. Konyak's three source images were cached and
inspected successfully, confirming the review cache workflow.
The older approved Commons batch still needs second review; its images are
hidden until then. Bulk Commons image fetching hit Wikimedia's 429 limit; the
reviewer must use the project's identifying User-Agent and low concurrency.
The Commons vetter now caches the source image for every model call in ignored
`work/commons-review/<sidecar-stem>/`, with the URL hash in its filename; the
second reviewer can inspect the same source image without another Wikimedia fetch.
Missing image files remain unreviewed rather than being approved from text.

In the first older-batch editorial pass, all seven model-accepted Bukharan Jewish
photos were inspected on a contact sheet. Five with a specific community link
and visible dress, music, or festival practice passed. A shared Central Asian
food photo and a Samarkand vessel labeled only as Uzbekistani did not. Five
Karakalpak candidates were inspected and rejected: a race photographed in
Karakalpakstan did not identify the participants as Karakalpak, while four
objects photographed in Samarkand had only country-level attribution. Wikimedia
initially returned 429 for the other four. On a later retry their images showed
an unattributed cradle and Nowruz dancers described only as Uzbekistani; those
four were rejected too. The status command now separates editorial rejections
from images still awaiting that review.

A second contact sheet covered 11 Central Asian photos. The Xinjiang Kazakh
yurt scene, a specifically captioned Kazakh eagle-hunting festival in Mongolia,
and specifically identified Kyrgyz women in traditional dress passed. A distant
Kyrgyz yurt scene did not show the craft clearly enough. Three eagle-hunting
photos categorized as Kyrgyzstani did not specifically identify the pictured
people as Kyrgyz. Four Pamiri candidates were rejected: three had only regional
or generic Tajikistani attribution, and the Cleveland Museum described the
fourth, a wedding veil, as worn by Tajik Turkmen rather than Pamiri people.

Nineteen T'boli and Yakan candidates were then checked against full contact
sheets and Commons descriptions. Nine T'boli images passed, mostly distinct
*t'nalak* textiles plus a museum object, brass belt, and clothing. A festival
parade image had no specific T'boli identification and failed. Four Yakan
images passed: one of two views of the same saddle panel, a sword, a textile
display, and a museum knife. The other saddle-panel view was redundant. Two
mixed-exhibition pictures were categorized as Yakan but did not identify their
pictured textiles; a celebrity portrait and a photo explicitly describing
Maranao people were also rejected.

For Fon, the Codex subscription judged 53 of 82 world-list candidates. All 51
initially selected images were viewed in contact sheets, and all 46 British
Museum source records were checked. The final gallery has 28 objects. Three
additional loaded records were marked rejected after older catalogue notes
showed uncertain or mixed ethnic attribution; their exclusions are also stored
for future pick runs. Four of six model-accepted Commons photos passed an
independent image-and-caption review. The generic Abomey Vodun scene and an
outsider printed picture of a Dahomean soldier were rejected. Unrelated
Folkways material was removed, and both profiles were rewritten from object
records. All 31 loaded images were uploaded to R2; only the 28 approved
objects enter the index.

Four more Southeast Asian Commons sidecars were checked against complete
contact sheets and source captions. Lao: four of nine passed (a museum
costume, salt gourd, and two specifically identified Lao houses); a second
view of the costume was redundant, and generic Laos village and Thai
bracelet photos lacked Lao ethnic attribution. Hmong: five of eight passed,
including clearly identified Hmong people in Vietnam, Laos, and Thailand and
silver earrings made by a Hmong refugee. The atlas currently has one Hmong
entry, so a specifically Hmong image from another country is relevant;
country-only or broad Miao labels are not enough. Iban: seven of eight
passed, including museum textiles, a hornbill figure, specifically identified
people in Sarawak and Indonesian Borneo, and a contemporary Iban object.
An image of severed heads attributed to a different people failed the
collection's object and image scope. All eight previously pending Toraja photos passed: each
showed a named Toraja ceremony, textile, house, tomb, or dress with a visible
subject and a supporting caption. The Ma'nene image depicts a deceased person
being dressed and is shown as ritual documentation.

The current Commons counts are 133 published, 77 model-accepted awaiting
editorial review, 54 editorially rejected, 309 model-rejected, and 710 awaiting
the model. The remaining 77 positives are hidden until checked.

Next: vet the remaining hidden Commons photos through the subscription CLI in
batches, review accepted images and their captions, then rebuild the index. A direct
`scrape_all.py` invocation still needs a follow-up `vet_images.py` run; the
index gate prevents unjudged records from being published in the meantime.
