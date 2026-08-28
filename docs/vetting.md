# Vetting — the quality gate

`scripts/vet_images.py` is how this atlas decides whether a record belongs. It shows every image to Claude Haiku via the Claude Code CLI (`claude --print --tools Read`, subscription-covered — never the paid API) and asks two questions:

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

## What the prompt encodes

Each of these rules exists because its absence produced a measured, specific failure. Do not remove one without re-running the calibration.

- **A European holding country is never grounds for rejection.** Europeana's location field is the *holding museum*. Treating it as origin sent Europeana's reject rate to 86% and discarded Iban pua kumbu, Batak ulos, Minangkabau songket. See [museums.md](museums.md).
- **Monumental architecture is in scope.** Mosques, temples, palaces, mausolea, forts, walled towns — including famous, imperially-patronised ones. Without this the judge invented a vernacular-vs-monumental line the atlas does not draw and dropped Hagia Sophia, Wat Phra Kaew, Bibi Khanym and Khulbuk.
- **Photographic style is never a reason to reject.** Staged, modern, touristic, charity or news photographs still evidence the pattern if the subject shows traditional dress or craft. Judge the subject, not the photographer's intent. Only reject when the actual subject is something else — a street market where a monument is mere backdrop.
- **Religious art made by the culture counts** regardless of how finely made. Ethiopian Orthodox painting on hand-woven cotton, Buddha figures, mosque tilework. Judge who made it, not what it depicts.
- **Ethnicity tie-break.** If the museum's own record *names* a different people, it is mis-filed → NO. If the group is merely unverifiable, keep it — we cannot tell neighbouring groups apart by eye either, and absence of proof is not evidence of a mistake.
- **Out of scope:** portable excavated antiquity (grave goods, cylinder seals, temple-sculpture fragments in museums), European fine art including named masters documenting the culture (Rubens' costume book), colonial exhibition material, maps and charts, portraits of named rulers, museum catalogue cards.

Tuning history, all measured on identical records: drop rate **44% → 28% → 26%**, each reduction traceable to removing one named over-strictness.

## Running it

```bash
# calibration — verdicts printed, nothing written
python scripts/vet_images.py --target library --limit 50 --seed 7777 --dry-run

# a real chunk, persisted
python scripts/vet_images.py --target library --limit 500 --workers 20

# one museum only, or one ethnicity
python scripts/vet_images.py --target library --source british_museum
python scripts/vet_images.py --target library --only uzbek
```

Throughput is ~50 records/minute at `--workers 20`. All records go through one global thread pool; an earlier per-file pool put a barrier between `metadata.json` files and pinned throughput at 14/min regardless of worker count.

**Failure mode to watch: quota exhaustion.** When the CLI starts failing, every call returns non-zero and the script records `vision_vetted: None` while the progress counter keeps climbing — a run can look healthy and produce nothing. On 2026-08-27 this happened after ~650 records and the remaining 3,338 were logged as errors. Watch the *verdicts*, not the counter. Resume is safe: records are skipped only on a real boolean verdict, so `None` records are retried automatically.

`build_index.py` consumes the results — it drops records whose `vision_vetted` is `False` and prefers `art_form_vision` over the rule-based classifier. `None` behaves exactly like never-vetted, so a failed run is harmless to the index.
