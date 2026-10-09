import { useEffect, useMemo, useRef, useState } from 'react';
import type { GlobePoint } from '../lib/types';

interface Props {
  points: GlobePoint[];
  onSelect: (key: string) => void;
  // The side panel covers the bottom-left corner on narrow screens.
  panelOpen: boolean;
}

type Mode = 'cultures' | 'images';

// [title, culture key, art form, tradition, image, object id | null], see scripts/sync-public.mjs.
type ImageRow = [string, string, string, string, string, string | null];

interface CultureHit {
  key: string;
  ethnicity: string;
  country: string;
  matched: string;
  via: 'name' | 'country' | 'tradition';
  count: number;
}

const PAGE = 100;

// Lowercase and drop diacritics, so "sami" finds "Sámi".
const fold = (s: string) => s.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();

// 0 exact, 1 prefix, 2 a word starts with it, 3 anywhere, -1 no match.
function rank(text: string, needle: string): number {
  const t = fold(text);
  if (t === needle) return 0;
  if (t.startsWith(needle)) return 1;
  const i = t.indexOf(needle);
  if (i < 0) return -1;
  return /[^a-z0-9]/.test(t[i - 1]) ? 2 : 3;
}

const imageSrc = (image: string) =>
  image.startsWith('http') || image.startsWith('/') ? image : `/${image.replace(/\\/g, '/')}`;

// A search box in the bottom-left corner. Cultures: every match shows as you
// type and a click goes straight to the culture. Images: every match shows as
// you type; a click opens the picture large, and "Go to culture" jumps from there.
export function SearchBox({ points, onSelect, panelOpen }: Props) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>('cultures');
  const [q, setQ] = useState('');
  const [rows, setRows] = useState<ImageRow[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const [picked, setPicked] = useState<ImageRow | null>(null);
  const [broken, setBroken] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const byKey = useMemo(() => new Map(points.map((p) => [p.key, p])), [points]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName ?? '');
      if (((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') || (e.key === '/' && !typing)) {
        e.preventDefault();
        setOpen(true);
      } else if (e.key === 'Escape') {
        if (picked) setPicked(null);
        else if (open) {
          if (q) setQ('');
          else setOpen(false);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [picked, q, open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // The image index is about a megabyte compressed: fetched the first time Images is used.
  useEffect(() => {
    if (mode !== 'images' || rows || loadError) return;
    fetch('/data/search-images.json')
      .then((r) => r.json())
      .then(setRows)
      .catch(() => setLoadError(true));
  }, [mode, rows, loadError]);

  useEffect(() => setShown(PAGE), [q, mode]);

  const needle = fold(q.trim());

  const cultureHits = useMemo<CultureHit[]>(() => {
    if (mode !== 'cultures' || !needle) return [];
    const scored: [number, CultureHit][] = [];
    for (const p of points) {
      const base = { key: p.key, ethnicity: p.ethnicity, country: p.country, count: p.object_count + (p.unvetted_count ?? 0) };
      const e = rank(p.ethnicity, needle);
      if (e >= 0) { scored.push([e, { ...base, matched: p.ethnicity, via: 'name' }]); continue; }
      const c = rank(p.country, needle);
      if (c >= 0) { scored.push([10 + c, { ...base, matched: p.country, via: 'country' }]); continue; }
      const trad = (p.seed_traditions || []).find((t) => fold(t).includes(needle));
      if (trad) scored.push([20 + rank(trad, needle), { ...base, matched: trad, via: 'tradition' }]);
    }
    // Names before countries before traditions; exact, prefix, word start, anywhere; then the larger collection.
    scored.sort((a, b) => a[0] - b[0] || b[1].count - a[1].count);
    return scored.map(([, h]) => h);
  }, [mode, points, needle]);

  // Folded text of every image row, built once the index has loaded.
  const haystack = useMemo(
    () => (rows ? rows.map((r) => {
      const p = byKey.get(r[1]);
      return fold(`${r[0]} ${r[2]} ${r[3]} ${p?.ethnicity ?? ''} ${p?.country ?? ''}`);
    }) : []),
    [rows, byKey],
  );

  const imageHits = useMemo<number[]>(() => {
    if (mode !== 'images' || !rows || !needle) return [];
    const words = needle.split(/\s+/).filter(Boolean);
    const hits: [number, number][] = [];
    for (let i = 0; i < rows.length; i++) {
      const h = haystack[i];
      if (!words.every((w) => h.includes(w))) continue;
      const t = fold(rows[i][0]);
      hits.push([words.reduce((s, w) => s + (t.includes(w) ? 1 : 0), 0), i]);
    }
    hits.sort((a, b) => b[0] - a[0] || a[1] - b[1]);
    return hits.map(([, i]) => i);
  }, [mode, rows, haystack, needle]);

  const total = mode === 'cultures' ? cultureHits.length : imageHits.length;
  const pickedCulture = picked ? byKey.get(picked[1]) : null;

  const goToCulture = () => {
    if (!picked) return;
    onSelect(picked[1]);
    setPicked(null);
  };

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label="Search"
          title="Search (/)"
          className={'sidebar-panel absolute bottom-16 left-3 z-30 flex items-center gap-2 rounded-full border border-dusk px-4 py-2.5 text-parchment sm:bottom-20 ' + (panelOpen ? 'max-lg:hidden' : '')}
        >
          <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="8.5" cy="8.5" r="5.5" /><path d="M13 13l4.5 4.5" /></svg>
          <span className="text-[11px] uppercase tracking-widest">Search</span>
        </button>
      )}
      {open && <div
        className={'absolute bottom-16 left-3 z-30 w-[min(360px,calc(100vw-1.5rem))] sm:bottom-20 ' + (panelOpen ? 'max-lg:hidden' : '')}
      >
        <div className="sidebar-panel flex flex-col overflow-hidden rounded-md border border-dusk">
          {needle && (
            <div className="max-h-[min(420px,50vh)] overflow-y-auto border-b border-dusk">
              {mode === 'images' && !rows && !loadError && <div className="sub-meta px-3 py-3 text-sm">Loading the image index…</div>}
              {mode === 'images' && loadError && <div className="sub-meta px-3 py-3 text-sm">The image index could not be loaded.</div>}
              {(mode === 'cultures' || rows) && total === 0 && <div className="sub-meta px-3 py-3 text-sm">No matches.</div>}
              {mode === 'cultures' && cultureHits.map((h) => (
                <button
                  key={h.key}
                  onClick={() => onSelect(h.key)}
                  className="flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left hover:bg-dusk/40"
                >
                  <div>
                    <div className="font-serif text-base">{h.ethnicity}</div>
                    <div className="sub-mono text-[10px] uppercase tracking-widest">
                      {h.country}
                      {h.via !== 'name' && <span className="ml-2 opacity-70">via {h.via}: {h.matched}</span>}
                    </div>
                  </div>
                  <div className="sub-mono shrink-0 text-[10px] uppercase tracking-widest">{h.count} obj</div>
                </button>
              ))}
              {mode === 'images' && imageHits.slice(0, shown).map((i) => {
                const r = rows![i];
                const p = byKey.get(r[1]);
                return (
                  <button
                    key={i}
                    onClick={() => { setPicked(r); setBroken(false); }}
                    className="block w-full px-3 py-2 text-left hover:bg-dusk/40"
                  >
                    <div className="font-serif text-base leading-snug">{r[0]}</div>
                    <div className="sub-mono text-[10px] uppercase tracking-widest">
                      {p ? `${p.ethnicity} · ${p.country}` : r[1]}{r[2] && r[2] !== 'commons' ? ` · ${r[2]}` : ''}
                    </div>
                  </button>
                );
              })}
              {mode === 'images' && imageHits.length > shown && (
                <button onClick={() => setShown((s) => s + PAGE)} className="sub-mono w-full px-3 py-2 text-[10px] uppercase tracking-widest hover:bg-dusk/40">
                  Show {Math.min(PAGE, imageHits.length - shown)} more of {imageHits.length - shown}
                </button>
              )}
            </div>
          )}
          <div className="flex items-center gap-2 p-2">
            <div className="mode-toggle flex shrink-0 items-center gap-1 rounded-full border border-dusk p-0.5">
              {(['cultures', 'images'] as Mode[]).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={'rounded-full px-2.5 py-1 text-[10px] uppercase tracking-widest transition ' + (mode === m ? 'bg-amber-500 text-ink' : 'text-parchment/70 hover:text-parchment')}
                >
                  {m}
                </button>
              ))}
            </div>
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={mode === 'cultures' ? 'Search cultures…' : 'Search images…'}
              className="search-input min-w-0 flex-1 bg-transparent px-1 py-1.5 text-base text-parchment outline-none"
            />
            {needle && <span className="sub-mono shrink-0 text-[10px] uppercase tracking-widest">{total}</span>}
            <button onClick={() => { setQ(''); setOpen(false); }} aria-label="Close search" className="close-btn shrink-0 rounded-full border px-2.5 py-1 text-sm leading-none">×</button>
          </div>
        </div>
      </div>}

      {picked && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.8)' }}
          onClick={() => setPicked(null)}
        >
          <div className="sidebar-panel flex max-h-full w-full max-w-[900px] flex-col rounded-md border border-dusk p-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex min-h-0 flex-1 items-center justify-center">
              {broken ? (
                <div className="sub-meta py-16 text-sm">This image no longer loads from its source.</div>
              ) : (
                <img
                  src={imageSrc(picked[4])}
                  alt={picked[0]}
                  onError={() => setBroken(true)}
                  className="max-h-[70vh] max-w-full object-contain"
                />
              )}
            </div>
            <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
              <div>
                <div className="font-serif text-lg leading-snug">{picked[0]}</div>
                <div className="sub-mono text-[10px] uppercase tracking-widest">
                  {pickedCulture ? `${pickedCulture.ethnicity} · ${pickedCulture.country}` : picked[1]}
                  {picked[2] && picked[2] !== 'commons' ? ` · ${picked[2]}` : ''}
                  {picked[3] ? ` · ${picked[3]}` : ''}
                </div>
              </div>
              <div className="flex gap-2">
                <button onClick={goToCulture} className="rounded-full bg-amber-500 px-4 py-2 text-[11px] uppercase tracking-widest text-ink">Go to culture</button>
                <button onClick={() => setPicked(null)} className="rounded-full border border-dusk px-4 py-2 text-[11px] uppercase tracking-widest">Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
