# Cloud vetting

The full `--force` re-vet is ~4,600 Sonnet judgements (4,508 records without a current-prompt verdict on 2026-09-24), ~15 hours of wall time locally at the rate limit ([vetting.md](vetting.md)). This runs the heavy part — the judgements — in a Claude Code cloud session, so it spends cloud session credit instead of local subscription usage. Everything else stays local.

The judge is the same as the local vetter's: the exact prompt from `vet_images.build_prompt`, the same model (`vet_images.MODEL`), the same parser (`vet_images.parse_reply`) and the same persistence (`vet_images.apply_verdict`). In the cloud, `scripts/cloud_vet_batch.py judge` runs one bare `claude --print` call per record with the image attached inline.

## Why this shape

Measured or read from the docs on 2026-09-24:

- The cloud session has a shell and the `claude` CLI (2.1.282 on 2026-09-24), so a plain script can call `claude --print` per record, as the local vetter does.
- **What a call costs is almost all Claude Code overhead, not the vetting prompt.** The prompt is ~2.5k tokens and a 1024 px image ~1k; everything else is system prompt, tool definitions, and CLAUDE.md files. Measured per record:

  | Shape | Cost per record | Tokens per call | Measured by |
  |---|---|---|---|
  | Subagents, 10 records each (pilot) | $0.08 | — | cloud credit, $9 for 117 |
  | One subagent per record (pilot2) | $0.13 | — | cloud credit, $15 for 117; main session grew to 270k tokens, woken once per finished subagent |
  | local default `claude --print --tools Read` | $0.41 | 58.7k, 2 turns | CLI-reported `total_cost_usd` |
  | lean: `--system-prompt`, `--tools Read`, empty MCP | $0.18 | 24.8k, 2 turns | same |
  | image inline, `--tools ""` | $0.12 | 18.4k, 1 turn | same — the rest is the user-level `~/.claude/CLAUDE.md` |
  | **image inline, `--tools ""`, `--setting-sources local`** | **$0.035** | **4.1k, 1 turn** | same, 20 records |

- There is no prompt-cache reuse between different records (0 cache reads across 3 consecutive different images): every call pays its full input once. A second call with the *same* image read 18k tokens from cache for $0.017, which says nothing about a real batch.
- Subagents launched from a cloud session run in the background even when asked to run in the foreground, so the main session wakes for every finished subagent and re-reads its whole context each time. That, and each subagent's own fixed context, is why both subagent shapes cost more than the bare call.
- The cloud environment's default network policy is an allowlist that excludes the museum image hosts; the environment needs **Custom** network access with the domains below.
- Sessions stop after a period of inactivity; no maximum length is documented. So work is split into batches of a few hundred records and committed as it goes.
- Pushes go to `claude/`-prefixed branches only.
- Routines draw ordinary subscription usage, not cloud session credit, so they are not used.

Whether `claude --print` inside a cloud session is billed to the cloud session credit at the rate it reports is **not yet measured**; the first `judge` batch settles it (credit before and after).

## Flow

```
local:  scripts/export_vet_batch.py        → data/vet_batches/<batch>.jsonl   (commit + push)
cloud:  scripts/cloud_vet_batch.py fetch   → work/img/ (≤1024 px), work/prompts/
cloud:  scripts/cloud_vet_batch.py judge   → work/replies/<key>.txt, work/judge_<batch>.jsonl
cloud:  scripts/cloud_vet_batch.py collect → data/vet_verdicts/<batch>.jsonl (commit + push to claude/…)
local:  git fetch + merge the branch
local:  scripts/apply_vet_verdicts.py data/vet_verdicts/<batch>.jsonl  → library metadata.json
```

A batch row carries the record id, a key (hash of metadata file + id — the same museum object can be filed under two ethnicities and each copy is judged separately), the rendered prompt and the image URLs: the R2 copy first when uploaded (same bytes as the local file), then the source museum. `work/` is gitignored scratch.

```bash
python scripts/export_vet_batch.py --name pilot --ids-file ids.json
python scripts/export_vet_batch.py --name b001 --todo --limit 200 --seed 1 --exclude-batches
python scripts/cloud_vet_batch.py judge b001 150     # at most 150 pending records this run
python scripts/apply_vet_verdicts.py data/vet_verdicts/pilot.jsonl --dry-run
python scripts/apply_vet_verdicts.py data/vet_verdicts/pilot.jsonl
```

`--todo` selects records without a current-prompt verdict (`vision_image` unset). `apply_vet_verdicts.py` records download failures as `vision_vetted: None` with a note, so they are retried like local failures.

`judge` replaces the prompt's first sentence ("Read the image at path …") with "The image is attached." and sends the image as a base64 content block over `--input-format stream-json`; the rest of the prompt is byte-identical to the local vetter's. It runs 3 workers, backs off on Sonnet's "temporarily limiting requests" bursts on the same schedule as `vet_images._ask_claude`, runs from a temporary directory so no project CLAUDE.md is discovered, and logs every raw result with `cost_usd` and `usage` to `work/judge_<batch>.jsonl`. Measured locally on 20 records: 36 s, $0.70 reported, 0 failures.

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

No environment variables, secrets or setup script: `cloud_vet_batch.py` is standard library only (Pillow, installed with pip in the session, adds downscaling) and every image URL is public. The "default list of common package managers" option is on, so `pip` works. Wikimedia answers 429 at 6 parallel downloads, so `fetch` spaces requests to one host 1 s apart and backs off on 429.

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
