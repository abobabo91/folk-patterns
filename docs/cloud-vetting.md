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

No environment variables, secrets or setup script: `cloud_vet_batch.py` is standard library only and every image URL is public. Wikimedia answers 429 at 6 parallel downloads, so `fetch` spaces requests to one host 1 s apart and backs off on 429.

## Procedure for the cloud session

The prompt that starts a session is: *"Follow docs/cloud-vetting.md, section 'Procedure for the cloud session', for batch `<batch>`."* The session then does:

1. `python scripts/cloud_vet_batch.py fetch <batch>` — report the fetched count and any failures.
2. `python scripts/cloud_vet_batch.py status <batch>` — writes `work/pending_<batch>.txt`, one key per line.
3. Split the pending keys into chunks of 10. Launch subagents with **model `sonnet`**, up to 5 at a time, one chunk each, with exactly this instruction (keys filled in):

   > You are one worker in an image-vetting job. For EACH key below, in order: (1) Read `work/prompts/<key>.txt`. (2) Do exactly what that prompt says — it tells you to look at the image `work/img/<key>.jpg`; Read it. (3) Write your answer, and nothing else, in the prompt's exact six-line format (REASON / BELONGS / ART_FORM / IMAGE / ERA / CONFIDENCE) to `work/replies/<key>.txt` with the Write tool. Judge every record on its own; an earlier record must not influence a later one. Do not edit any other file. Keys: `<key1> … <key10>`

4. After every wave of subagents: `python scripts/cloud_vet_batch.py collect <batch>`, then `git add data/vet_verdicts/<batch>.jsonl`, commit and push to the session's `claude/` branch.
5. Repeat 2–4 until `status` reports 0 pending with an image. Do not judge any record yourself — only subagents judge, so every verdict comes from the same instruction.
6. Finish with a final `collect`, commit and push, and report: records, replies, download failures, and the branch name.

## Pilot — 2026-09-24

Batch `pilot`: the 110 records already judged by eye in [vetting.md](vetting.md) (60 calibration + 50 random) — 117 rows, since 7 of those objects are filed under two ethnicities. Tested locally end to end before the cloud run: `fetch` 117/117 (after adding the per-host throttle; before it, Wikimedia returned 429 on 2), one Sonnet reply collected and parsed by `apply_vet_verdicts.py --dry-run`.

What the pilot is to measure: agreement of cloud-subagent verdicts with the local Sonnet verdicts and with the by-eye labels; whether 10 records per subagent causes anchoring between records; wall time; and the credit spent, read from the claude.ai usage page before and after.
