# Cloud vetting

The full `--force` re-vet is ~4,600 Sonnet judgements (4,508 records without a current-prompt verdict on 2026-09-24), ~15 hours of wall time locally at the rate limit ([vetting.md](vetting.md)). This runs the heavy part — the judgements — in a Claude Code cloud session, so it spends cloud session credit instead of local subscription usage. Everything else stays local.

The judge is the same as the local vetter's: both call `vet_judge.judge` (`scripts/vet_judge.py` — the prompt, the model and the CLI call), and verdicts go through the same parser (`vet_images.parse_reply`) and the same persistence (`vet_images.apply_verdict`). In the cloud, `scripts/cloud_vet_batch.py judge` runs that call once per record.

## Why this shape

Measured or read from the docs on 2026-09-24:

- The cloud session has a shell and the `claude` CLI (2.1.282 on 2026-09-24), so a plain script can call `claude --print` per record, as the local vetter does.
- **What a call costs was mostly Claude Code overhead, then the uncached prompt.** Measured per record:

  | Shape | Cost per record | Tokens per call | Measured by |
  |---|---|---|---|
  | Subagents, 10 records each (pilot) | $0.08 | — | cloud credit, $9 for 117 |
  | One subagent per record (pilot2) | $0.13 | — | cloud credit, $15 for 117; main session grew to 270k tokens, woken once per finished subagent |
  | local default `claude --print --tools Read` | $0.41 | 58.7k, 2 turns | CLI-reported `total_cost_usd` |
  | lean: `--system-prompt`, `--tools Read`, empty MCP | $0.18 | 24.8k, 2 turns | same |
  | image inline, `--tools ""` | $0.12 | 18.4k, 1 turn | same — the rest is the user-level `~/.claude/CLAUDE.md` |
  | **image inline, `--tools ""`, `--setting-sources local`** | **$0.035** | **4.1k, 1 turn** | same, 20 records |
  | the same, in the cloud (`judge`, batch b001) | $0.025 | — | cloud credit, $5 for 197 incl. the main session; the script reported $4.49 |
  | long rules moved to the system prompt, cached | $0.0116 | 3,445 cached | CLI-reported, 117 records |
  | **short rules (~3k chars) in the system prompt, cached — the current `vet_judge.judge`** | **$0.011** | **1,199 cached** | CLI-reported, 117 records (Pilot 3) |

- **The prompt cache only reuses a prefix that is identical across calls.** With the rules in the user message after the image, 3 consecutive different records read 0 tokens from cache. With the rules as the system prompt and the record + image as the user message, every call after the first reads the whole system prompt from cache. Once cached, the image and the record block (~350–1,500 tokens, written fresh each call) are most of the cost, so shortening the rules from ~10k to ~3k characters saved only ~7% more — the short prompt is kept because it judges as well ([vetting.md](vetting.md#short-cached-prompt--2026-09-24)).
- Subagents launched from a cloud session run in the background even when asked to run in the foreground, so the main session wakes for every finished subagent and re-reads its whole context each time. That, and each subagent's own fixed context, is why both subagent shapes cost more than the bare call.
- The cloud environment's default network policy is an allowlist that excludes the museum image hosts; the environment needs **Custom** network access with the domains below.
- Sessions stop after a period of inactivity; no maximum length is documented. So work is split into batches of a few hundred records and committed as it goes.
- Pushes go to `claude/`-prefixed branches only.
- Routines draw ordinary subscription usage, not cloud session credit, so they are not used.

`claude --print` inside a cloud session is billed to the cloud session credit, at the rate it reports: batch `b001` reported $4.49 for 197 calls and the credit went $226 → $221, with weekly plan usage unchanged (58%). A cloud call was cheaper than the same call locally ($0.023 vs $0.035); why is not established — the user-level CLAUDE.md is already excluded by `--setting-sources local` in both.

## Flow

```
local:  scripts/export_vet_batch.py        → data/vet_batches/<batch>.jsonl   (commit + push)
cloud:  scripts/cloud_vet_batch.py fetch   → work/img/ (≤1024 px), work/prompts/
cloud:  scripts/cloud_vet_batch.py judge   → work/replies/<key>.txt, work/judge_<batch>.jsonl
cloud:  scripts/cloud_vet_batch.py collect → data/vet_verdicts/<batch>.jsonl (commit + push to claude/…)
local:  git fetch + merge the branch
local:  scripts/apply_vet_verdicts.py data/vet_verdicts/<batch>.jsonl  → library metadata.json
```

A batch row carries the record id, a key (hash of metadata file + id — the same museum object can be filed under two ethnicities and each copy is judged separately), the record text for the user message (`prompt`) and the image URLs: the R2 copy first when uploaded (same bytes as the local file), then the source museum. `work/` is gitignored scratch.

```bash
python scripts/export_vet_batch.py --name pilot --ids-file ids.json
python scripts/export_vet_batch.py --name b001 --todo --limit 200 --seed 1 --exclude-batches
python scripts/cloud_vet_batch.py judge b001 150     # at most 150 pending records this run
python scripts/apply_vet_verdicts.py data/vet_verdicts/pilot.jsonl --dry-run
python scripts/apply_vet_verdicts.py data/vet_verdicts/pilot.jsonl
```

`--todo` selects records without a current-prompt verdict (`vision_image` unset). `apply_vet_verdicts.py` records download failures as `vision_vetted: None` with a note, so they are retried like local failures.

`judge` calls `vet_judge.judge` per record: `claude --print --setting-sources local --tools "" --strict-mcp-config` with an empty MCP config, `--system-prompt-file` holding the fixed rules, and a stream-json user message with the record text and the image as a base64 block. It runs 3 workers, backs off on Sonnet's "temporarily limiting requests" bursts (30 → 300 s), runs from a temporary directory so no project CLAUDE.md is discovered, and logs every raw attempt with `cost_usd` and `usage` to `work/judge_<batch>.jsonl`. A batch exported before the prompt split (its `prompt` starts "Read the image at path") is refused; re-export it. Measured locally on 117 records: 160 s, $1.28 reported, 0 failures.

## Cloud environment

Network access **Custom**, allowed domains (every image host in the library, including redirect targets, probed 2026-09-24):

```
media.britishmuseum.org
upload.wikimedia.org
api.europeana.eu
openaccess-cdn.clevelandart.org
framemark.vam.ac.uk
images.metmuseum.org
ids.si.edu
sammlung.mak.at
temp.sammlung.mak.at
collections.smvk.se
iiif.micr.io
collectie.wereldculturen.nl
collectie.wereldmuseum.nl
gallica.bnf.fr
sgdap.girona.cat
<the R2 public bucket host — public_base_url in the vault>
```

No environment variables, secrets or setup script: `cloud_vet_batch.py` is standard library only (Pillow, installed with pip in the session, adds downscaling) and every image URL is public. The "default list of common package managers" option is on, so `pip` works. `fetch` spaces requests to one host 1 s apart and backs off on 429. **Wikimedia answers 429 to a User-Agent without contact information** and 200 to the same request once the UA carries the repo URL (measured 2026-09-24 on `upload.wikimedia.org` originals, one request each); the UA in `cloud_vet_batch.py` and `vet_images.py` carries it. Before that fix, 2–4% of every cloud batch (8–17 of ~1,030) failed on Wikimedia 429 even after a second `fetch`; with it, a local retry of all 47 fetched 47 / 47.

`media.britishmuseum.org` serves its certificate without the intermediate (Corporation Service Company RSA OV SSL CA). Browsers and Windows fetch it through AIA; Python in the cloud sandbox does not, and `crt.sectigo.com` is not reachable from there, so every British Museum download failed `CERTIFICATE_VERIFY_FAILED` in the pilot's first fetch (30 of 117). The intermediate is committed at `scripts/certs/extra-intermediates.pem` and added to the default trust store by `fetch`; verified 2026-09-24 against certifi's roots alone (fails without the file, downloads with it). Another host with the same fault gets its intermediate appended to that file.

## Procedure for the cloud session

Start the session with the main model on the lowest effort — it only runs scripts. A new session can open in plan mode; the prompt says not to plan. The prompt is: *"Do not plan; execute directly. Follow docs/cloud-vetting.md, section 'Procedure for the cloud session', for batch `<batch>`."* The session then does:

1. `pip install pillow` (so `fetch` downscales to 1024 px), then `python scripts/cloud_vet_batch.py fetch <batch>` — report the fetched count and any failures.
2. `python scripts/cloud_vet_batch.py judge <batch> 150` — at most 150 records per run (~3 min at the measured rate), so one run stays inside a single command's time limit.
3. `python scripts/cloud_vet_batch.py collect <batch>`, then `git add data/vet_verdicts/<batch>.jsonl`, commit and push to the session's `claude/` branch.
4. Repeat 2–3 until `judge` reports 0 pending. Do not judge any record yourself and do not read the replies — every verdict comes from the script.
5. Report: records, replies, download failures, failed judgements, the last "reported cost" line of `judge`, and the branch name.

## Pilot — 2026-09-24

Batch `pilot`: the 110 records already judged by eye in [vetting.md](vetting.md) (60 calibration + 50 random) — 117 rows, since 7 of those objects are filed under two ethnicities. Judged by Sonnet subagents, 10 records each, in cloud session `claude/happy-mayer-amhm1a` (main model Sonnet 5 on high effort, 15 subagents in 4 waves). Measured:

| | |
|---|---|
| Records judged | 117 / 117, 0 unparsed replies (first fetch lost the 30 British Museum images to the TLS fault above; rerun after the fix) |
| Verdicts | 90 kept, 27 dropped |
| BELONGS vs local Sonnet | 110 / 116 agree |
| ART_FORM / IMAGE / ERA vs local Sonnet | 108 / 109 / 104 of 116 — the same order of drift as local Sonnet against itself ([vetting.md](vetting.md)) |
| BELONGS vs by-eye labels | 55 / 60; 2 of the 5 are labels made under the old rule that excluded excavated material (a Book of the Dead, a Ban Chiang jar), which the cloud now correctly keeps as `era: archaeological` |
| Wall time | ~35 min including ~6 min diagnosing the TLS fault; a 5-subagent wave of 50 records took ~2 min |
| Credit | **$9** ($250 → $241); weekly plan usage unchanged (56% before and after) |

The six BELONGS disagreements with local Sonnet, read one by one: two where the cloud is right (a Shan zinme longyi and a Shan ikat both filed under Bamar → NO, while the same ikat under `_regional` Myanmar → YES), one where it is wrong (livestock in a field near Aksum kept as `photo`), and three borderline (a near-blank faded sketch dropped, a patola-style double ikat under Balinese dropped, an Ewe kente under Ashanti kept).

Anchoring: one subagent wrote "the same red … longyi" for the second copy of an object filed under two ethnicities, so a worker does notice repeats inside its chunk. It still judged each copy against its own filing, and no disagreement clusters by chunk.

## Pilot 2 — 2026-09-24

The same 117 records, one subagent per record (a Read/Write-only Sonnet agent), images downscaled to 1024 px, main session on low effort, in cloud session `claude/exciting-heisenberg-pthl8k`. Credit **$15** ($241 → $226). Verdicts: BELONGS agrees with pilot on 114 / 117 and with local Sonnet on 110 / 116, IMAGE 110 / 116 — so **downscaling to 1024 px does not change the verdicts**; the cost went up, for the reasons under "Why this shape".

The bare `judge` call on 20 of these records (locally): BELONGS 20 / 20 vs pilot, 19 / 20 vs local Sonnet (the Aksum livestock again), IMAGE 20 / 20, ERA 17 / 20.

## Batch b001 — 2026-09-24

200 records without a current-prompt verdict (`--todo --seed 1`), judged by `judge` in cloud session `claude/brave-mccarthy-rv3ms3` (main model Sonnet 5, low effort). 197 judged, 0 failed judgements, 3 downloads failed on Wikimedia 429 even on a second `fetch` (recorded as retryable). ~9.5 min end to end. $5 of credit. 158 kept, 39 dropped.

The drops, read: ethnonym collisions and out-of-scope pictures — a Cruikshank caricature under Khmer, a Tintoretto copy and a Carven fashion sketch under San, a ukiyo-e print under Maasai, an Ottoman costume album under Chin, placeholder icons, a newspaper front page, a Baroque siege etching under Afar, a modern museum building in Dushanbe, a tourist snapshot at an airport. One debatable: a Mughal-style album portrait dropped under Hazara. A random 14 of the keeps are all correct (Gur-e-Amir tilework, a Burmese court painting, Yoruba adire and strip cloth, a Kazakh felt, a Hmong appliqué, a Gelede mask); one category slip — Vietnamese lacquer boxes as `metalwork`.

## Pilot 3 — 2026-09-24

The 117 pilot records re-exported (`pilot3`) and judged locally with the current `vet_judge.judge`: the short system prompt, cached. 117 / 117 replies, 0 failures, **$1.27** reported ($0.011 per record); every call after the first reads the system prompt from cache (1,199 tokens before the last prompt edits). Agreement: BELONGS 113 / 117 with the cloud pilot and 112 / 116 with local Sonnet; ART_FORM / IMAGE / ERA 84 / 88 / 85 of the 89 both runs keep; by-eye labels 55 / 60, three of the misses being wrong labels. Read in detail in [vetting.md](vetting.md#short-cached-prompt--2026-09-24). The verdicts in `data/vet_verdicts/pilot3.jsonl` are a measurement, not applied — the pilot's are.

## Batch b002 — 2026-09-24

200 records (`--todo --seed 2`), judged in cloud session `claude/elegant-lovelace-8t0fa4` with the short cached prompt (main model Sonnet 5, low effort) — the first cloud batch on it. 198 judged in two `judge` runs, 0 failed judgements, 2 downloads lost to Wikimedia 429 (retryable). The first run reported $1.42 for 150 records. Credit **$221 → $218** for the whole session (the page shows whole dollars), i.e. ~$0.015 per record including the main session, against $0.025 for b001.

Reading the 38 drops found the category-driven drops described in [vetting.md](vetting.md#short-cached-prompt--2026-09-24); after the prompt fix they were re-judged locally ($0.41) and 7 flipped to YES. Applied: `b002.jsonl`, then `b002-drops-rejudged.jsonl` — 167 kept, 31 dropped. The right drops, read: European engravings and a Rubens costume sketch, ukiyo-e under Maasai, Indian deity paintings under Fang, Dutch travel engravings under Cham, a Manila newspaper, a map of German East Africa, a necktie of Thai silk by a French designer, Akdamar's Armenian church under Kurdish, Bamum residents under Fulani, a Batak jacket under Minangkabau.

## Full run — 2026-09-24

The remaining 4,108 records as four batches of ~1,030 (`b003`–`b006`, `--todo --seed 3 --exclude-batches`), one cloud session each, all four running at once (main model Sonnet 5, low effort). About 75 minutes end to end, a third of it `fetch`.

| Batch | Judged | Failed judgements | Download failures | Reported | Kept / dropped | Branch |
|---|---|---|---|---|---|---|
| b003 | 1,013 | 0 | 17 | $9.61 | 814 / 199 | `claude/hopeful-sagan-3kxdmw` |
| b004 | 1,021 | 0 | 9 | $9.68 | 813 / 208 | `claude/trusting-gates-sxe02q` |
| b005 | 1,022 | 0 | 8 | $9.62 | 852 / 170 | `claude/trusting-carson-g509t1` |
| b006 | 1,010 | 0 | 8 | $9.43 | 821 / 189 | `claude/vet-b006` |

- **Credit $218 → $171: $47 for 4,066 records, ~$0.0116 per record** including the four main sessions; the judge calls alone reported $38.34 ($0.0094 per record).
- **Four sessions × 3 workers ran with no rate-limit failure** — 12 concurrent Sonnet calls, 0 failed judgements across 4,066.
- The 42 download failures plus the 5 left from b001/b002 were re-exported (`r001`, `--todo`), fetched locally with the fixed User-Agent (47 / 47) and judged locally ($0.59): 40 kept, 7 dropped.
- Not every session follows step 3 unprompted: the b005 session looped `judge` without committing between runs until told to. Check that a batch branch appears on the remote after the first `judge` run.
- Read before applying, per batch: every drop the category pattern could explain, 15 random drops and 12–20 random keeps. All drops read were right except a domed mudbrick building in Garagos dropped under Nubian as "not a ceramic object" (b003) — the category-driven drop the prompt now guards against, once in ~4,000 — and a gold-tooled Persian album binding dropped as "not the paintings themselves". Keeps read: all right.

