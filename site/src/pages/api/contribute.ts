// Astro API route — hybrid serverless endpoint that receives a
// contribution submission (form fields + image), validates, and stages it
// under R2 `pending/` where the local `scripts/review_contributions.py`
// CLI reviews and approves it into the library.
//
// Env vars expected (set in Vercel dashboard AND in a local `.env` file
// at site/ for `astro dev`):
//   R2_ENDPOINT              e.g. https://<account>.r2.cloudflarestorage.com
//   R2_ACCESS_KEY_ID
//   R2_SECRET_ACCESS_KEY
//   R2_BUCKET                folk-patterns
//
// No third-party services beyond R2. Every submission is manually
// reviewed, so a honeypot field is our only bot defence; add IP throttling
// later if abuse becomes a real problem.

import type { APIRoute } from 'astro';
import { S3Client, PutObjectCommand } from '@aws-sdk/client-s3';
import crypto from 'node:crypto';

export const prerender = false;

const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const ALLOWED_MIMES = new Set(['image/jpeg', 'image/png']);

function s3(): S3Client {
  const endpoint = import.meta.env.R2_ENDPOINT || process.env.R2_ENDPOINT;
  const accessKeyId = import.meta.env.R2_ACCESS_KEY_ID || process.env.R2_ACCESS_KEY_ID;
  const secretAccessKey = import.meta.env.R2_SECRET_ACCESS_KEY || process.env.R2_SECRET_ACCESS_KEY;
  if (!endpoint || !accessKeyId || !secretAccessKey) {
    throw new Error('R2 credentials missing from env');
  }
  return new S3Client({
    region: 'auto',
    endpoint,
    credentials: { accessKeyId, secretAccessKey },
  });
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export const POST: APIRoute = async ({ request }) => {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return jsonResponse(400, { error: 'malformed submission' });
  }

  // Honeypot — real users skip this field, dumb bots fill it in.
  if ((form.get('website') as string | null)?.trim()) {
    // Silently accept-and-drop so the bot thinks it worked.
    return jsonResponse(200, { ok: true, id: 'ignored' });
  }

  const title = (form.get('title') as string | null)?.trim();
  const ethnicityKey = (form.get('ethnicity_key') as string | null)?.trim();
  if (!title) return jsonResponse(400, { error: 'missing field: title' });
  if (!ethnicityKey) return jsonResponse(400, { error: 'missing field: ethnicity_key' });

  const file = form.get('image');
  if (!(file instanceof File)) return jsonResponse(400, { error: 'no image uploaded' });
  if (!ALLOWED_MIMES.has(file.type)) {
    return jsonResponse(400, { error: 'image must be JPEG or PNG' });
  }
  if (file.size > MAX_IMAGE_BYTES) {
    return jsonResponse(413, { error: 'image too large (max 5MB)' });
  }

  const id = `contrib_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
  const ext = file.type === 'image/png' ? 'png' : 'jpg';
  const imageBytes = Buffer.from(await file.arrayBuffer());

  const rawIp = (request.headers.get('x-forwarded-for') || '').split(',')[0].trim();
  const ipHash = crypto.createHash('sha256').update(rawIp).digest('hex').slice(0, 16);

  const meta = {
    id,
    submitted_at: new Date().toISOString(),
    submitter_ip_hash: ipHash,
    submitter_email: (form.get('submitter_email') as string | null) || null,
    submitter_name: (form.get('submitter_name') as string | null) || null,
    ethnicity_key: ethnicityKey,
    art_form: (form.get('art_form') as string | null) || 'unclassified',
    tradition: (form.get('tradition') as string | null) || null,
    title,
    description: (form.get('description') as string | null) || null,
    source_url: (form.get('source_url') as string | null) || null,
    credit: (form.get('credit') as string | null) || null,
    license: (form.get('license') as string | null) || 'unknown',
    image_key: `pending/${id}.${ext}`,
    image_mime: file.type,
    image_bytes: imageBytes.length,
  };

  const client = s3();
  const bucket = import.meta.env.R2_BUCKET || process.env.R2_BUCKET || 'folk-patterns';
  try {
    await client.send(new PutObjectCommand({
      Bucket: bucket,
      Key: meta.image_key,
      Body: imageBytes,
      ContentType: file.type,
    }));
    await client.send(new PutObjectCommand({
      Bucket: bucket,
      Key: `pending/${id}.json`,
      Body: JSON.stringify(meta, null, 2),
      ContentType: 'application/json',
    }));
  } catch (e: unknown) {
    console.error('R2 upload failed', e);
    return jsonResponse(500, { error: 'upload failed, please try again' });
  }

  return jsonResponse(200, { ok: true, id });
};
