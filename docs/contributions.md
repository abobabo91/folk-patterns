# User contributions

Users can submit records through the `/contribute` page. Every submission
is manually reviewed via a local CLI before it appears on the site.

## Architecture

```
/contribute (Astro page)
      │  multipart POST
      ▼
/api/contribute (Astro serverless route, prerender=false)
      │  validates: honeypot, required fields, image MIME, image size (≤5MB)
      ▼
R2 bucket `folk-patterns`
      pending/<contrib_id>.jpg
      pending/<contrib_id>.json      (submitter metadata)
      │
      ▼  (async, on developer machine)
python scripts/review_contributions.py
      approve → moves image + record into library/…, appends metadata.json,
                also uploads image to R2 at the permanent library key
      reject  → deletes pending copies, logs reason to
                data/contributions_reject.log
```

Approved records show up on the site after the next
`python scripts/build_index.py`.

## Local dev

The endpoint reads R2 credentials from `site/.env` (git-ignored). To create
it from the vault:

```bash
python -c "
import tomllib
v = tomllib.loads(open('tools/vault/vault.toml','r',encoding='utf-8').read())
r2 = v['apis']['cloudflare_r2']
lines = [
    f\"R2_ENDPOINT={r2['s3_endpoint']}\",
    f\"R2_ACCESS_KEY_ID={r2['access_key_id']}\",
    f\"R2_SECRET_ACCESS_KEY={r2['secret_access_key']}\",
    f\"R2_BUCKET={r2.get('bucket_name','folk-patterns')}\",
]
open('site/.env','w',encoding='utf-8').write('\n'.join(lines) + '\n')
"
```

Then `cd site && npm run dev` — the endpoint is at
`http://localhost:4321/api/contribute`.

## Vercel deploy

Set the same four env vars in the Vercel project's dashboard under
**Settings → Environment Variables**:

- `R2_ENDPOINT`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET`

Values from `tools/vault/vault.toml` under `[apis.cloudflare_r2]`. Scope
to Production only.

Astro's build produces the serverless function automatically because
`astro.config.mjs` uses the `@astrojs/vercel/serverless` adapter with
`output: 'hybrid'`.

## Reviewing pending submissions

```bash
python scripts/review_contributions.py
```

For each pending submission it downloads the image to a temp file, opens
it in the default viewer, prints metadata, and prompts:

- `[a]pprove` — writes to `library/<region>/<country>/<ethnicity>/<art_form>/<tradition>/`, uploads image to R2 permanent path, deletes pending copies
- `[r]eject` — asks for a reason, deletes pending copies, appends to `data/contributions_reject.log`
- `[e]dit` — tweak title / description / tradition / art_form / credit / license before approving
- `[s]kip` — leave in pending, review later
- `[q]uit`

After approving one or more, run `python scripts/build_index.py` and
`(cd site && node scripts/sync-public.mjs)` so the new records appear on
the site.

## Anti-abuse

Only defence right now is a honeypot field (invisible `<input name="website">`
that bots fill in; the endpoint silently accepts-and-drops those). No rate
limiting. Every submission goes through manual review, so junk never
reaches users.

If bot submissions become a real problem, add either:
- IP throttling using a small R2 counter object per IP hash, or
- Cloudflare Turnstile (`data-sitekey` on the form + Turnstile SDK verify
  in the endpoint).

## Smoke test

```bash
# 1. dev server up
cd site && npm run dev &

# 2. open the form
open http://localhost:4321/contribute

# 3. submit a test record

# 4. verify it landed
python -c "
import sys; sys.path.insert(0,'src')
from folk_patterns.r2 import client
s3 = client()
print(s3.list_objects_v2(Bucket='folk-patterns', Prefix='pending/'))
"

# 5. review + reject
python scripts/review_contributions.py
```
