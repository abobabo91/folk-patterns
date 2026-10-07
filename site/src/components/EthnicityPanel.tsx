import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Lightbox, LightboxContext, lightboxClick, useLightboxOpen } from './Lightbox';
import type { LightboxItem } from './Lightbox';
import { marked } from 'marked';
import type { EthnicityShard, GlobePoint, SlimObject, CommonsPhoto, UnescoIchEntry, FolkwaysEntry, UnvettedItem, UnvettedShard } from '../lib/types';

// Configure marked to be safe-ish for our own content.
marked.setOptions({ gfm: true, breaks: false });

function stripFrontmatter(md: string): string {
  if (md.startsWith('---')) {
    const end = md.indexOf('\n---', 3);
    if (end !== -1) return md.slice(end + 4).trimStart();
  }
  return md;
}

// Slug used for anchor IDs and mini-nav links. Must match what markdown->html
// gets: our custom renderer adds id attributes to h2 elements.
function slugifyHeading(s: string): string {
  return s.toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .trim();
}

// Extract h2 headings from the raw markdown (before HTML conversion) so the
// mini-nav can list section jump-links.
function extractSections(md: string): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  const lines = md.split('\n');
  for (const line of lines) {
    const m = line.match(/^##\s+(.+?)\s*$/);
    if (m) {
      const label = m[1].trim();
      out.push({ id: slugifyHeading(label), label });
    }
  }
  return out;
}

// Custom marked renderer that adds id attributes to h2 headings, so anchor
// jumps work.
const renderer = new marked.Renderer();
const origHeading = renderer.heading.bind(renderer);
renderer.heading = ({ tokens, depth, raw }: any) => {
  const text = (tokens ?? []).map((t: any) => t.raw ?? t.text ?? '').join('');
  if (depth === 2) {
    const id = slugifyHeading(text || raw || '');
    return `<h2 id="${id}">${marked.parseInline(text)}</h2>`;
  }
  return origHeading({ tokens, depth, raw });
};

interface Props {
  point: GlobePoint | null;
  shard: EthnicityShard | null;
  onClose: () => void;
}

const AF_ORDER = ['textile', 'garment', 'architectural', 'wallpaper', 'ceramic', 'jewelry', 'metalwork', 'arms', 'masks-ritual', 'sculpture', 'instruments', 'painting-mss', 'household', 'unclassified', 'photo'];

// Words that add no signal in a tile caption ("Uzbek Tribe. Ladies stocking
// boots." → drop the "Uzbek Tribe." prefix). Applied case-insensitively.
const GENERIC_TILE_PREFIXES = /^(uzbek tribe[\.\-\s]*|uzbek people[\.\-\s]*|the )/i;

function tileLabel(obj: SlimObject): string {
  // Prefer the specific object title over the tradition-slug seed tag.
  // Falls back to tradition, then art_form.
  const raw = (obj.title || '').trim();
  if (raw) {
    const cleaned = raw
      .replace(GENERIC_TILE_PREFIXES, '')
      .replace(/\.(jpe?g|png|tif{1,2}|gif|webp|bmp)$/i, '')   // strip Commons file extension
      .trim();
    // Truncate long titles for the tile chip.
    return cleaned.length > 34 ? cleaned.slice(0, 32).trimEnd() + '…' : cleaned;
  }
  return obj.tradition || obj.art_form || '';
}


// Flat per-art_form gallery: shows the first INITIAL tiles, with a "Show N
// more" button revealing the rest. The build step already interleaves by
// tradition, so the initial 9 naturally contain one representative per
// sub-category before repeating any.
const INITIAL_PER_BUCKET = 9;

// Full-screen mode widens the panel to the whole window; the galleries then
// use more columns instead of three huge tiles.
const FullContext = createContext(false);
const gridCols = (full: boolean) => 'grid gap-1.5 ' + (full ? 'grid-cols-4 md:grid-cols-6' : 'grid-cols-3');

const tileImage = (obj: SlimObject) =>
  !obj.image ? '' : obj.image.startsWith('http') || obj.image.startsWith('/') ? obj.image : `/${obj.image.replace(/\\/g, '/')}`;

function ArtFormBucket({ af, label, items }: { af: string; label: string; items: SlimObject[] }) {
  const [expanded, setExpanded] = useState(false);
  // A museum image that fails to load (moved, deleted, blocked) hides its tile.
  const [failed, setFailed] = useState<Set<string>>(new Set());
  const visible = items.filter((obj) => !failed.has(obj.id));
  const shown = expanded ? visible : visible.slice(0, INITIAL_PER_BUCKET);
  const hidden = visible.length - shown.length;
  const full = useContext(FullContext);
  const open = useLightboxOpen();
  if (!visible.length) return null;
  return (
    <section>
      <h3 className="mb-3 flex items-baseline gap-2 font-serif text-xl font-medium">
        {label}
        <span className="sub-mono font-mono text-[10px] uppercase tracking-widest">
          {items.length}
        </span>
      </h3>
      <div className={gridCols(full)}>
        {shown.map((obj, i) => {
          // Load the top-6 tiles eagerly (they're above the fold on most
          // panel scrolls) and mark the first tile fetchpriority=high so
          // the browser doesn't queue it behind lazy-loaded siblings.
          // Tiles 7-9 in the initial view + all Show-More tiles stay lazy.
          const eager = !expanded && i < 6;
          // Commons photos have no object page (build_index writes none for
          // them); they open their Commons file page instead.
          const external = obj.source === 'commons' && !!obj.object_url;
          return (
          <a
            key={obj.id}
            href={external ? obj.object_url : `/object/${obj.id}`}
            {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
            onClick={lightboxClick(open, `r:${obj.id}`)}
            className="card group relative aspect-square block bg-gradient-to-br from-dusk/40 to-dusk/10 animate-pulse-slow overflow-hidden"
            title={obj.title || obj.tradition || ''}
          >
            {obj.image && (
              <img
                src={tileImage(obj)}
                alt={obj.title || ''}
                loading={eager ? 'eager' : 'lazy'}
                fetchpriority={i === 0 ? 'high' : (eager ? 'auto' : 'low')}
                decoding="async"
                onLoad={(e) => { (e.currentTarget.parentElement as HTMLElement).classList.remove('animate-pulse-slow'); }}
                onError={() => setFailed((cur) => new Set(cur).add(obj.id))}
                className="h-full w-full object-contain p-1 transition group-hover:scale-105"
              />
            )}
            <span className="tile-tag absolute bottom-1 left-1 rounded-sm bg-ink/70 px-1.5 py-0.5 text-[9px] uppercase tracking-widest">
              {tileLabel(obj)}
            </span>
          </a>
          );
        })}
      </div>
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="sub-mono mt-2 text-[10px] uppercase tracking-widest text-parchment/60 hover:text-amber-400 transition"
        >
          + Show {hidden} more
        </button>
      )}
    </section>
  );
}

const AF_LABEL: Record<string, string> = {
  textile: 'Textile',
  garment: 'Garment',
  architectural: 'Architectural',
  wallpaper: 'Wallpaper',
  ceramic: 'Ceramic',
  jewelry: 'Jewelry',
  metalwork: 'Metalwork',
  arms: 'Arms & armour',
  'masks-ritual': 'Masks & ritual objects',
  instruments: 'Musical instruments',
  'painting-mss': 'Painting & Manuscripts',
  sculpture: 'Sculpture',
  household: 'Household objects',
  unclassified: 'Other',
  photo: 'Photographs',
};

function MediaSection({ shard, unreviewed = false }: { shard: EthnicityShard; unreviewed?: boolean }) {
  const [failed, setFailed] = useState<Set<string>>(new Set());
  const photos = (shard.commons_photos ?? []).filter((p) => !failed.has(p.thumb_url ?? ''));
  const ich = shard.unesco_ich ?? [];
  const folkways = shard.folkways ?? [];
  const full = useContext(FullContext);
  const open = useLightboxOpen();
  return (
    <div className="mt-10 border-t border-dusk pt-6 space-y-8">
      {photos.length > 0 && (
        <section data-af="commons">
          <h3 className="mb-3 font-serif text-lg font-medium">
            Photographs (Wikimedia Commons{unreviewed ? ', unreviewed' : ''})
          </h3>
          <div className={gridCols(full)}>
            {photos.map((p) => (
              <a
                key={p.page_url ?? p.title ?? Math.random()}
                href={p.page_url ?? undefined}
                target="_blank"
                rel="noopener noreferrer"
                onClick={lightboxClick(open, `c:${p.page_url ?? p.thumb_url}`)}
                className="card group relative aspect-square block"
                title={[p.title, p.credit].filter(Boolean).join(' — ')}
              >
                {p.thumb_url && (
                  <img
                    src={p.thumb_url}
                    onError={() => setFailed((cur) => new Set(cur).add(p.thumb_url ?? ''))}
                    alt={p.title ?? ''}
                    loading="lazy"
                    className="h-full w-full object-cover transition group-hover:scale-105"
                  />
                )}
                {p.license && (
                  <span className="tile-tag absolute bottom-1 left-1 rounded-sm px-1.5 py-0.5 text-[9px] uppercase tracking-widest">
                    {p.license}
                  </span>
                )}
              </a>
            ))}
          </div>
          <p className="sub-mono mt-2 text-[10px]">
            {unreviewed
              ? "Picked automatically from this people's Commons categories and Wikipedia article; not checked by a person."
              : 'Sourced from the Wikipedia article for this group + curated Commons categories.'}
          </p>
        </section>
      )}

      {ich.length > 0 && (
        <section>
          <h3 className="mb-3 font-serif text-lg font-medium">
            UNESCO Intangible Cultural Heritage <span className="sub-mono font-mono text-[10px] uppercase tracking-widest">{ich.length} {ich.length === 1 ? 'entry' : 'entries'}</span>
          </h3>
          <ul className="space-y-2">
            {ich.map((e) => (
              <li key={e.code ?? e.title ?? Math.random()} className="card p-3">
                <div className="flex items-baseline justify-between gap-2">
                  <a
                    href={e.unesco_url ?? undefined}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-serif text-base capitalize hover:text-amber-400"
                  >
                    {e.title}
                  </a>
                  <span className="sub-mono font-mono text-[10px] uppercase tracking-widest">
                    {e.code}
                  </span>
                </div>
                {e.description && (
                  <p className="sub-meta mt-1 text-sm">{e.description}</p>
                )}
              </li>
            ))}
          </ul>
          <p className="sub-mono mt-2 text-[10px]">
            Linked to this community. Each opens its official UNESCO record.
          </p>
        </section>
      )}

      {folkways.length > 0 && (
        <section>
          <h3 className="mb-3 font-serif text-lg font-medium">
            Recordings (Smithsonian) <span className="sub-mono font-mono text-[10px] uppercase tracking-widest">{folkways.length}</span>
          </h3>
          <ul className="space-y-1.5">
            {folkways.map((f) => (
              <li key={f.record_url ?? f.title ?? Math.random()} className="text-[13px]">
                <a
                  href={f.record_url ?? undefined}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-parchment hover:text-amber-400"
                >
                  {f.title}
                </a>
                {f.unit && (
                  <span className="sub-mono ml-2 font-mono text-[10px] uppercase tracking-widest">
                    {f.unit}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

const UNVETTED_PAGE_SIZE = 24;

function UnvettedGrid({ items }: { items: UnvettedItem[] }) {
  const [shown, setShown] = useState(INITIAL_PER_BUCKET);
  const [failed, setFailed] = useState<Set<string>>(new Set());
  const visible = items.filter((item) => !failed.has(`${item.source}:${item.id}`));
  const slice = visible.slice(0, shown);
  const hidden = visible.length - slice.length;
  const full = useContext(FullContext);
  const open = useLightboxOpen();
  return (
    <>
      <div className={gridCols(full)}>
        {slice.map((item) => {
          const key = `${item.source}:${item.id}`;
          return (
            <a
              key={key}
              href={item.object_url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={lightboxClick(open, `u:${key}`)}
              className="card group relative aspect-square block overflow-hidden border border-parchment/15 opacity-80"
              title={item.title}
            >
              <img
                src={item.image}
                alt={item.title}
                loading="lazy"
                decoding="async"
                onError={() => setFailed((current) => new Set(current).add(key))}
                className="h-full w-full object-contain p-1 transition group-hover:scale-105"
              />
              {/* Long museum descriptions are clamped to two lines so they
                  never cover the photo; the full text is in the hover title. */}
              <span className="tile-tag absolute bottom-1 left-1 right-1 line-clamp-2 w-fit rounded-sm bg-ink/70 px-1.5 py-0.5 text-[9px] leading-tight">
                {item.title}
              </span>
            </a>
          );
        })}
      </div>
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setShown((current) => current + UNVETTED_PAGE_SIZE)}
          className="sub-mono mt-2 text-[10px] uppercase tracking-widest text-parchment/50 hover:text-amber-400 transition"
        >
          Show more ({hidden} left)
        </button>
      )}
    </>
  );
}

// The culture's unreviewed objects (museum records matched by text only),
// fetched with the panel so each category shows next to its text.
function useUnvetted(ethKey: string | undefined, count: number): UnvettedShard | null {
  const [data, setData] = useState<UnvettedShard | null>(null);
  useEffect(() => {
    setData(null);
    if (!ethKey || !count) return;
    let live = true;
    fetch(`/data/unvetted/${ethKey}.json`)
      .then((r) => (r.ok ? (r.json() as Promise<UnvettedShard>) : null))
      .then((d) => { if (live) setData(d); })
      .catch(() => {});
    return () => { live = false; };
  }, [ethKey, count]);
  return data;
}

// Unreviewed objects of one category; "Other" also takes the uncategorised.
function unvettedFor(data: UnvettedShard | null, af: string): UnvettedItem[] {
  if (!data) return [];
  const items = data.buckets[af] ?? [];
  return af === 'unclassified' ? [...items, ...(data.other ?? [])] : items;
}

// One category: the reviewed gallery, with the unreviewed objects behind a
// toggle below it; with nothing reviewed, the unreviewed ones shown directly.
function CategoryBlock({ af, reviewed, unreviewed }: { af: string; reviewed?: SlimObject[]; unreviewed: UnvettedItem[] }) {
  const label = AF_LABEL[af] ?? af;
  if (reviewed?.length) {
    return (
      <div className="mt-3 mb-6" data-af={af}>
        <ArtFormBucket af={af} label={label} items={reviewed} />
        {unreviewed.length > 0 && (
          <details className="mt-3">
            <summary className="sub-mono cursor-pointer text-[10px] uppercase tracking-widest text-parchment/60 hover:text-amber-400">
              Unreviewed ({unreviewed.length})
            </summary>
            <div className="mt-3"><UnvettedGrid items={unreviewed} /></div>
          </details>
        )}
      </div>
    );
  }
  if (!unreviewed.length) return null;
  return (
    <div className="mt-3 mb-6" data-af={af}>
      <section>
        <h3 className="mb-3 flex items-baseline gap-2 font-serif text-xl font-medium">
          {label}
          <span className="sub-mono font-mono text-[10px] uppercase tracking-widest">{unreviewed.length}</span>
          <span className="sub-mono font-mono text-[10px] uppercase tracking-widest text-amber-400/80">Unreviewed</span>
        </h3>
        <UnvettedGrid items={unreviewed} />
      </section>
    </div>
  );
}

// Map writeup section headings (h2 or h3) to art_form bucket keys. When a
// section header matches, we inject the matching art_form gallery right
// after that section body — so architectural images sit next to the
// "Architecture" text, textile images next to "Textile & pattern
// traditions", etc. Instead of the old "all text, then all images at
// bottom" layout.
const SECTION_TO_ART_FORMS: Record<string, string[]> = {
  // h3 headings inside "Material culture"
  'textile & pattern traditions': ['textile'],
  'clothing & dress': ['garment'],
  'architecture': ['architectural'],
  'ceramics, metalwork & everyday objects': ['ceramic', 'metalwork', 'household'],
  'jewelry & body adornment': ['jewelry'],
  // h2 headings
  'oral tradition & literature': ['painting-mss'],
  'music & performance': ['instruments'],
  'festivals & rituals': ['masks-ritual'],
};

// Photographs are their own category with their own section, placed before
// the glossary / sources (not inside Festivals: most are dress and daily life).
const PHOTO_SECTION = { id: 'photographs', label: 'Photographs' };
const _isBackMatter = (heading: string) => /^(glossary|sources)/i.test(heading.trim());

function _lookupBuckets(headingText: string): string[] {
  const key = headingText.trim().toLowerCase().replace(/\s+/g, ' ');
  return SECTION_TO_ART_FORMS[key] || [];
}

// Split raw markdown into chunks, each chunk starting at a h2/h3 heading.
// Returns [{ level, text, body }] where body is the markdown paragraphs
// between this heading and the next one.
function _splitByHeading(md: string): { level: number; text: string; body: string }[] {
  const lines = md.split('\n');
  const chunks: { level: number; text: string; body: string }[] = [];
  let cur: { level: number; text: string; lines: string[] } | null = null;
  for (const line of lines) {
    const h = line.match(/^(##+)\s+(.+?)\s*$/);
    if (h) {
      if (cur) chunks.push({ level: cur.level, text: cur.text, body: cur.lines.join('\n') });
      cur = { level: h[1].length, text: h[2], lines: [line] };
    } else if (cur) {
      cur.lines.push(line);
    } else {
      // Preamble (no heading yet) — treat as an unheaded chunk
      cur = { level: 0, text: '', lines: [line] };
    }
  }
  if (cur) chunks.push({ level: cur.level, text: cur.text, body: cur.lines.join('\n') });
  return chunks;
}

function WriteupSection({ markdown, shard, unv }: { markdown: string; shard: EthnicityShard; unv: UnvettedShard | null }) {
  const stripped = useMemo(() => stripFrontmatter(markdown), [markdown]);
  const chunks = useMemo(() => _splitByHeading(stripped), [stripped]);
  const buckets = shard.art_form_buckets ?? {};
  const has = (af: string) => (buckets[af]?.length ?? 0) > 0 || unvettedFor(unv, af).length > 0;
  const hasPhotos = has('photo');
  const sections = useMemo(() => {
    const s = extractSections(stripped);
    if (!hasPhotos) return s;
    const i = s.findIndex((x) => _isBackMatter(x.label));
    return i < 0 ? [...s, PHOTO_SECTION] : [...s.slice(0, i), PHOTO_SECTION, ...s.slice(i)];
  }, [stripped, hasPhotos]);
  const backIdx = chunks.findIndex((c) => c.level === 2 && _isBackMatter(c.text));
  const photoAt = hasPhotos ? (backIdx < 0 ? chunks.length : backIdx) : -1;

  // Track which art_form buckets we've rendered inline so we can render the
  // leftovers (unclassified, photo, etc.) as a trailing gallery.
  const bucketsUsed = new Set<string>();
  if (hasPhotos) bucketsUsed.add('photo');
  const photoBlock = hasPhotos ? (
    <div key="photographs" id={PHOTO_SECTION.id} className="scroll-mt-4">
      <div className="mt-6 mb-6">
        <CategoryBlock af="photo" reviewed={buckets['photo']} unreviewed={unvettedFor(unv, 'photo')} />
      </div>
    </div>
  ) : null;

  const rendered = chunks.map((c, idx) => {
    const html = marked.parse(c.body, { renderer }) as string;
    const bucketKeys = _lookupBuckets(c.text);
    return (
      <div key={idx}>
        <div className="prose-writeup" dangerouslySetInnerHTML={{ __html: html }} />
        {bucketKeys.map((bk) => {
          if (!has(bk)) return null;
          bucketsUsed.add(bk);
          return <CategoryBlock key={bk} af={bk} reviewed={buckets[bk]} unreviewed={unvettedFor(unv, bk)} />;
        })}
      </div>
    );
  });

  // Any bucket that didn't match a section header — render at the end so
  // nothing is hidden. (photo / documentary photos, unclassified.)
  const trailing = AF_ORDER.filter((af) => has(af) && !bucketsUsed.has(af));

  return (
    <div className="mt-8 border-t border-dusk pt-6">
      {sections.length > 1 && (
        <nav className="mini-nav mb-6 flex flex-wrap gap-x-3 gap-y-1 text-[10px] font-mono uppercase tracking-widest">
          {sections.map((s) => (
            <a key={s.id} href={`#${s.id}`} className="text-parchment/50 hover:text-amber-400 transition">
              {s.label}
            </a>
          ))}
        </nav>
      )}
      <div>{photoAt < 0 ? rendered : [...rendered.slice(0, photoAt), photoBlock, ...rendered.slice(photoAt)]}</div>
      {trailing.length > 0 && (
        <div className="mt-6">
          {trailing.map((af) => (
            <CategoryBlock key={af} af={af} reviewed={buckets[af]} unreviewed={unvettedFor(unv, af)} />
          ))}
        </div>
      )}
    </div>
  );
}

export function EthnicityPanel({ point, shard, onClose }: Props) {
  const isOpen = !!point;
  const unvettedOnly = !!(point?.unvetted_only || shard?.unvetted_only);
  const unvettedCount = shard?.unvetted_count ?? 0;
  const unv = useUnvetted(shard ? point?.key : undefined, unvettedCount);
  const [full, setFull] = useState(false);
  const [lb, setLb] = useState<{ items: LightboxItem[]; index: number } | null>(null);
  useEffect(() => { if (!isOpen) setFull(false); }, [isOpen]);
  useEffect(() => setLb(null), [point?.key]);

  // Every image of the culture, in the order its categories appear on the
  // page (read from the data-af blocks), reviewed before unreviewed — so the
  // arrows also reach tiles still behind "show more" or a closed toggle.
  const openLightbox = useCallback((key: string) => {
    const buckets = shard?.art_form_buckets ?? {};
    const order = [...new Set([...document.querySelectorAll<HTMLElement>('aside [data-af]')].map((el) => el.dataset.af!))];
    const items: LightboxItem[] = [];
    for (const af of order) {
      if (af === 'commons') {
        for (const ph of shard?.commons_photos ?? []) {
          if (!ph.thumb_url) continue;
          items.push({ key: `c:${ph.page_url ?? ph.thumb_url}`, image: ph.thumb_url, title: ph.title || '', label: 'Wikimedia Commons',
            unreviewed: !!(shard?.unvetted_only), site: ph.page_url, details: null });
        }
        continue;
      }
      const label = AF_LABEL[af] ?? af;
      for (const obj of buckets[af] ?? []) {
        if (!obj.image) continue;
        const commons = obj.source === 'commons';
        items.push({ key: `r:${obj.id}`, image: tileImage(obj), title: obj.title || obj.tradition || '', label, unreviewed: false,
          site: obj.object_url, details: commons ? null : `/object/${obj.id}` });
      }
      for (const it of unvettedFor(unv, af)) {
        items.push({ key: `u:${it.source}:${it.id}`, image: it.image, title: it.title, label, unreviewed: true, site: it.object_url, details: null });
      }
    }
    const index = items.findIndex((x) => x.key === key);
    if (index >= 0) setLb({ items, index });
  }, [shard, unv]);

  // Escape closes the panel, except while typing (the search box takes it).
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key !== 'Escape' || e.defaultPrevented || t?.closest('input, textarea, [contenteditable]')) return;
      onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);
  return (
    <FullContext.Provider value={full}>
    <LightboxContext.Provider value={openLightbox}>
    <aside
      className={
        'sidebar-panel fixed right-0 top-0 z-30 h-screen w-full overflow-y-auto border-l border-dusk bg-night/95 backdrop-blur-xl transition-transform duration-300 ' +
        (full ? 'max-w-none ' : 'max-w-[520px] ') +
        (isOpen ? 'translate-x-0' : 'translate-x-full')
      }
    >
      {point && (
        // Zero-height sticky row: the buttons stay in the corner while the
        // panel scrolls, without pushing the content down.
        <div className="sticky top-0 z-20 flex h-0 items-start justify-end gap-2 pr-6 pt-6">
          <button
            onClick={() => setFull((f) => !f)}
            className="close-btn rounded-full border border-dusk bg-night px-2 py-1 text-[11px] shadow-sm"
            aria-label={full ? 'Exit full screen' : 'Full screen'}
            title={full ? 'Exit full screen' : 'Full screen'}
          >
            {full ? '⤡' : '⤢'}
          </button>
          <button
            onClick={onClose}
            className="close-btn rounded-full border border-dusk bg-night px-2 py-1 text-[11px] shadow-sm"
            aria-label="Close (Esc)"
            title="Close (Esc)"
          >
            ✕
          </button>
        </div>
      )}
      {point && (
        <div className={'p-8' + (full ? ' mx-auto max-w-6xl' : '')}>
          <div className="flex items-start justify-between pr-10">
            <div>
              <div className="sub-mono font-mono text-[10px] uppercase tracking-widest">
                {point.country} · {point.homeland_place ?? point.region}
              </div>
              <h2 className="mt-1 font-serif text-4xl font-medium leading-tight">{point.ethnicity}</h2>
              {unvettedOnly ? (
                <>
                  {unvettedCount > 0 ? (
                    <p className="sub-meta mt-2 text-sm">{unvettedCount} objects · <span className="text-amber-400">unreviewed</span></p>
                  ) : (
                    <p className="mt-2 text-sm text-amber-400">
                      No museum objects found yet
                      {shard?.commons_photos?.length ? ` · ${shard.commons_photos.length} unreviewed photos` : ''}
                    </p>
                  )}
                </>
              ) : (
                <p className="sub-meta mt-2 text-sm">
                  {point.object_count} object{point.object_count === 1 ? '' : 's'}
                  {unvettedCount > 0 && <> · {unvettedCount} <span className="text-amber-400">unreviewed</span></>}
                </p>
              )}
            </div>
          </div>

          {/* Seed traditions */}
          {!unvettedOnly && point.seed_traditions.length > 0 && (
            <div className="mt-6 flex flex-wrap gap-1.5">
              {point.seed_traditions.map((t) => (
                <span key={t} className="tag">{t}</span>
              ))}
            </div>
          )}

          {/* Writeup — interleaves art_form galleries inline (Textile tiles
              inside the "Textile & pattern traditions" section, etc.) via
              WriteupSection. Any bucket not matched by a section is rendered
              as a trailing gallery.
              When there's no writeup yet (new region, or ethnicity we
              haven't written up), fall back to rendering all art_form
              buckets in order so tiles still appear on the page. */}
          {shard?.writeup_markdown ? (
            <WriteupSection markdown={shard.writeup_markdown} shard={shard} unv={unv} />
          ) : shard ? (
            <div className="mt-8 border-t border-dusk pt-6">
              {AF_ORDER.map((af) => (
                <CategoryBlock key={af} af={af} reviewed={shard.art_form_buckets?.[af]} unreviewed={unvettedFor(unv, af)} />
              ))}
            </div>
          ) : null}
          {shard && unvettedCount > 0 && !unv && <div className="sub-mono mt-4 text-sm">Loading unreviewed objects…</div>}

          {/* Media: Commons photos + UNESCO ICH + Folkways audio */}
          {!unvettedOnly && shard && (shard.commons_photos?.length || shard.unesco_ich?.length || shard.folkways?.length) ? (
            <MediaSection shard={shard} />
          ) : null}
          {unvettedOnly && shard?.commons_photos?.length ? <MediaSection shard={shard} unreviewed /> : null}


          {!shard && (
            <div className="sub-mono mt-8 text-sm">Loading…</div>
          )}
          {shard && !unvettedOnly && shard.object_count === 0 && !shard.writeup_markdown && (
            <p className="sub-meta text-sm italic">
              No objects indexed yet for this ethnicity. Try running the scraper
              with a broader seed, or add local museum sources.
            </p>
          )}
        </div>
      )}
    </aside>
    {lb && <Lightbox items={lb.items} index={lb.index} onIndex={(index) => setLb({ ...lb, index })} onClose={() => setLb(null)}
      onBroken={(i, dir) => {
        // Drop the broken image and show its neighbour in the direction of travel.
        const items = lb.items.filter((_, j) => j !== i);
        if (!items.length) return setLb(null);
        setLb({ items, index: dir > 0 ? i % items.length : (i - 1 + items.length) % items.length });
      }} />}
    </LightboxContext.Provider>
    </FullContext.Provider>
  );
}
