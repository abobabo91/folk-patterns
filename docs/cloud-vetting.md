# Cloud vetting

The full `--force` re-vet is ~4,600 Sonnet judgements, ~15 hours of wall time locally at the rate limit ([vetting.md](vetting.md)). This runs the heavy part — the judgements — in a Claude Code cloud session, so it spends cloud session credit instead of local subscription usage. Everything else stays local.

The judge is the same as the local vetter's: the exact prompt from `vet_images.build_prompt`, the same parser (`vet_images.parse_reply`) and the same persistence (`vet_images.apply_verdict`). The only difference is who executes the prompt: a Sonnet subagent in the cloud instead of `claude --print` locally. Whether that difference changes verdicts is what the pilot measures.

## Why this shape

Measured or read from the docs on 2026-09-24:

- A cloud session gives no shell and there is no documented `claude` CLI inside it, so `vet_images.py` cannot run there as it does locally. **Subagents do work in cloud sessions**, so the cloud session fans records out to Sonnet subagents.
- The cloud environment's default network policy is an allowlist that excludes the museum image hosts; the environment needs **Custom** network access with the domains below.
- Sessions stop after a period of inactivity; no maximum length is documented. So work is split into batches of a few hundred records and committed as it goes.
- Pushes go to `claude/`-prefixed branches only.
- Routines draw ordinary subscription usage, not cloud session credit, so they are not used.

## Flow

```
local:  scripts/export_vet_batch.py      → data/vet_batches/<batch>.jsonl   (commit + push)
cloud:  scripts/cloud_vet_batch.py fetch → work/img/, work/prompts/
cloud:  Sonnet subagents                 → work/replies/<key>.txt
cloud:  scripts/cloud_vet_batch.py collect → data/vet_verdicts/<batch>.jsonl (commit + push to claude/…)
local:  git fetch + merge the branch
local:  scripts/apply_vet_verdicts.py data/vet_verdicts/<batch>.jsonl  → library metadata.json
```

A batch row carries the record id, a key (hash of metadata file + id — the same museum object can be filed under two ethnicities and each copy is judged separately), the rendered prompt and the image URLs: the R2 copy first when uploaded (same bytes as the local file), then the source museum. `work/` is gitignored scratch.

```bash
python scripts/export_vet_batch.py --name pilot --ids-file ids.json
python scripts/export_vet_batch.py --name b001 --todo --limit 500 --seed 1 --exclude-batches
python scripts/apply_vet_verdicts.py data/vet_verdicts/pilot.jsonl --dry-run
python scripts/apply_vet_verdicts.py data/vet_verdicts/pilot.jsonl
```

`--todo` selects records without a current-prompt verdict (`vision_image` unset). `apply_vet_verdicts.py` records download failures as `vision_vetted: None` with a note, so they are retried like local failures.

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

Start the session with the main model on the lowest effort — it only runs scripts and launches subagents. The prompt is: *"Follow docs/cloud-vetting.md, section 'Procedure for the cloud session', for batch `<batch>`."* The session then does:

1. `pip install pillow` (so `fetch` downscales to 1024 px), then `python scripts/cloud_vet_batch.py fetch <batch>` — report the fetched count and any failures.
2. `python scripts/cloud_vet_batch.py status <batch>` — writes `work/pending_<batch>.txt`, one key per line.
3. Take the next 10 pending keys. In **one** message, launch 10 subagents of type **`image-vetter`** ([.claude/agents/image-vetter.md](../.claude/agents/image-vetter.md): Sonnet, Read and Write only), **in the foreground** (`run_in_background: false`), one key each, with the prompt being just the key. They run in parallel and return together, so the main session wakes once per wave, not once per record. Each replies `done`; do not read or restate verdicts.
4. After every 3 waves: `python scripts/cloud_vet_batch.py collect <batch>`, then `git add data/vet_verdicts/<batch>.jsonl`, commit and push to the session's `claude/` branch.
5. Repeat 2–4 until `status` reports 0 pending with an image. Do not judge any record yourself — only subagents judge, so every verdict comes from the same instruction.
6. Finish with a final `collect`, commit and push, and report: records, replies, download failures, and the branch name.

One record per subagent: a subagent that judges several records carries all earlier ones in its context and pays for them again on every step.

## Pilot — 2026-09-24

Batch `pilot`: the 110 records already judged by eye in [vetting.md](vetting.md) (60 calibration + 50 random) — 117 rows, since 7 of those objects are filed under two ethnicities. Tested locally end to end before the cloud run: `fetch` 117/117 (after adding the per-host throttle; before it, Wikimedia returned 429 on 2), one Sonnet reply collected and parsed by `apply_vet_verdicts.py --dry-run`.

Run in cloud session `claude/happy-mayer-amhm1a`, main model Sonnet 5, 15 subagents in 4 waves. Results, measured:

| | |
|---|---|
| Records judged | 117 / 117, 0 unparsed replies (first fetch lost the 30 British Museum images to the TLS fault above; rerun after the fix) |
| Verdicts | 90 kept, 27 dropped |
| BELONGS vs local Sonnet | 110 / 116 agree |
| ART_FORM / IMAGE / ERA vs local Sonnet | 108 / 109 / 104 of 116 — the same order of drift as local Sonnet against itself ([vetting.md](vetting.md)) |
| BELONGS vs by-eye labels | 55 / 60; 2 of the 5 are labels made under the old rule that excluded excavated material (a Book of the Dead, a Ban Chiang jar), which the cloud now correctly keeps as `era: archaeological` |
| Wall time | ~35 min including ~6 min diagnosing the TLS fault; a 5-subagent wave of 50 records took ~2 min |
| Credit | **$9** ($250 → $241), ~$0.08 per record including the orchestrator and the debugging; weekly plan usage unchanged (56% before and after) |

The six BELONGS disagreements with local Sonnet, read one by one: two where the cloud is right (a Shan zinme longyi and a Shan ikat both filed under Bamar → NO, while the same ikat under `_regional` Myanmar → YES), one where it is wrong (livestock in a field near Aksum kept as `photo`), and three borderline (a near-blank faded sketch dropped, a patola-style double ikat under Balinese dropped, an Ewe kente under Ashanti kept).

Anchoring: one subagent wrote "the same red … longyi" for the second copy of an object filed under two ethnicities, so a worker does notice repeats inside its chunk. It still judged each copy against its own filing, and no disagreement clusters by chunk.

Projection: at $0.08 per record the $241 left covers ~3,000 records, not the full ~4,600. Where the cost goes (orchestrator context at 148k tokens by the end, subagent context growing through a chunk of 10, image size) is not yet split out.

Operational: the session opened in plan mode and waited for approval; accepting with "auto mode" let it run unattended afterwards.
