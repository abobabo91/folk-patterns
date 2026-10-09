// Mirrors the parts of ../data/ the site reads into ./public/data/, so Astro
// serves them at /data/... in dev and build: index.json, globe.json and the
// ethnicities/ and objects/ shards. Nothing else — data/ also holds scrape
// caches, probes and vetting transcripts, which must not be published.
// The shard folders are replaced, not merged: a shard build_index.py no
// longer writes (a dropped object) must not survive as a page.
// public/data/world-countries.geojson is the site's own file and is kept.
// On Vercel ../data does not exist; the uploaded public/data is used as is.
import { cp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { existsSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

// The public mirror includes ethnicity/object shards and the optional
// unvetted/ and territories/ shards; scrape caches and vetting transcripts
// remain private.

const here = dirname(fileURLToPath(import.meta.url));
const siteRoot = resolve(here, '..');
const src = resolve(siteRoot, '..', 'data');
const dst = join(siteRoot, 'public', 'data');
const FILES = ['index.json', 'globe.json'];
const DIRS = ['ethnicities', 'objects'];
const OPTIONAL_DIRS = ['unvetted', 'territories'];
const SEARCH_FILE = 'search-images.json';
const KEEP = new Set([...FILES, SEARCH_FILE, ...DIRS, ...OPTIONAL_DIRS, 'world-countries.geojson']);

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
for (const d of OPTIONAL_DIRS) {
  await rm(join(dst, d), { recursive: true, force: true });
  if (existsSync(join(src, d))) await cp(join(src, d), join(dst, d), { recursive: true });
}
// search-images.json: one row per published image (museum objects and Commons photos) for the
// search box's Images mode: [title, culture key, art form, tradition, image, object id | null].
const rows = [];
for (const f of (await readdir(join(src, 'ethnicities'))).sort()) {
  const shard = JSON.parse(await readFile(join(src, 'ethnicities', f), 'utf-8'));
  for (const [artForm, objs] of Object.entries(shard.art_form_buckets || {})) {
    for (const o of objs) if (o.image) rows.push([o.title || o.tradition || artForm, shard.key, artForm, o.tradition || '', o.image, o.id]);
  }
  for (const ph of shard.commons_photos || []) {
    if (ph.thumb_url) rows.push([ph.title || '', shard.key, 'commons', '', ph.thumb_url, null]);
  }
}
await writeFile(join(dst, SEARCH_FILE), JSON.stringify(rows));
console.log(`wrote ${SEARCH_FILE}: ${rows.length} images`);
console.log(`synced ${FILES.length} files and ${[...DIRS, ...OPTIONAL_DIRS].join(', ')} from ${src}`);
