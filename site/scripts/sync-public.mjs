// Mirrors the parts of ../data/ the site reads into ./public/data/, so Astro
// serves them at /data/... in dev and build: index.json, globe.json and the
// ethnicities/ and objects/ shards. Nothing else — data/ also holds scrape
// caches, probes and vetting transcripts, which must not be published.
// The shard folders are replaced, not merged: a shard build_index.py no
// longer writes (a dropped object) must not survive as a page.
// public/data/world-countries.geojson is the site's own file and is kept.
// On Vercel ../data does not exist; the uploaded public/data is used as is.
import { cp, mkdir, rm } from 'node:fs/promises';
import { existsSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const siteRoot = resolve(here, '..');
const src = resolve(siteRoot, '..', 'data');
const dst = join(siteRoot, 'public', 'data');
const FILES = ['index.json', 'globe.json'];
const DIRS = ['ethnicities', 'objects'];
const KEEP = new Set([...FILES, ...DIRS, 'world-countries.geojson']);

if (!existsSync(src)) {
  console.log(`skip: ${src} does not exist, using public/data as uploaded`);
  process.exit(0);
}
await mkdir(dst, { recursive: true });
for (const name of readdirSync(dst)) {
  if (!KEEP.has(name)) await rm(join(dst, name), { recursive: true, force: true });
}
for (const f of FILES) await cp(join(src, f), join(dst, f), { force: true });
for (const d of DIRS) {
  await rm(join(dst, d), { recursive: true, force: true });
  await cp(join(src, d), join(dst, d), { recursive: true });
}
console.log(`synced ${FILES.length} files and ${DIRS.join(', ')} from ${src}`);
