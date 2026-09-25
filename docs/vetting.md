# Vetting — the quality gate

`scripts/vet_images.py` is how this atlas decides whether a record belongs. It shows every image to Claude Sonnet via the Claude Code CLI (one bare `claude --print` per image, the image inline, subscription-covered — never the paid API; the call and the prompt are `scripts/vet_judge.py`, shared with the [cloud path](cloud-vetting.md)) and asks for a verdict with its reasoning — BELONGS and ART_FORM below, plus IMAGE and ERA (see [Scope-aligned prompt](#scope-aligned-prompt--calibration-2026-09-24)). The two original questions:

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

`build_index.py` consumes the results — it drops records whose `vision_vetted` is `False` and prefers `art_form_vision` over the rule-based classifier. `None` behaves exactly like never-vetted, so a failed run is harmless to the index.

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

Categories (`art_form_vision`): textile, garment, jewelry (shown as adornment), ceramic, metalwork, **arms**, **masks-ritual**, sculpture, **instruments**, household, architectural, painting-mss, photo, unclassified. Slugs stay stable because `classify.py`, the library folders and the site share them.

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

The full re-vet is done: on 2026-09-24 every one of the 4,625 records then in the library carried a verdict from the current prompt — 3,755 kept, 870 dropped (19%); IMAGE good 4,428 / weak 141 / unusable 56; ERA traditional 3,556 / modern 579 / archaeological 490. The run: [cloud-vetting.md](cloud-vetting.md#full-run--2026-09-24). `build_index.py` treats the verdict as final for every source, and drops that belong to another culture are re-filed (above).

1. **Wire the vetter into `add_culture.py` / `scrape_all.py`** so new material arrives judged. Until then, after any scrape run `python scripts/vet_images.py --target library` — `build_index.py` keeps records that have no verdict yet.
2. **Consider Armenian and Shan as new cultures** — they are the only peoples with 10+ good objects waiting in the drops.
