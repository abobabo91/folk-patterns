import { useEffect, useMemo, useRef, useState } from 'react';
import type { GlobePoint } from '../lib/types';

interface Props {
  points: GlobePoint[];
  onSelect: (key: string) => void;
}

interface SearchHit {
  key: string;
  ethnicity: string;
  country: string;
  hit_type: 'ethnicity' | 'tradition' | 'country';
  matched: string;
  count: number;
  unvetted?: boolean;
}

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

// Very small overlay. Cmd/Ctrl-K to open; type to filter across ethnicity,
// country, and seed_traditions. Enter picks the top hit.
export function SearchOverlay({ points, onSelect }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen(true);
      } else if (e.key === 'Escape' && open) {
        setOpen(false);
      } else if (e.key === '/' && !open && document.activeElement?.tagName !== 'INPUT') {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 30);
  }, [open]);

  const hits = useMemo<SearchHit[]>(() => {
    const needle = fold(q.trim());
    if (!needle) return [];
    const scored: [number, SearchHit][] = [];
    for (const p of points) {
      const base = {
        key: p.key, ethnicity: p.ethnicity, country: p.country,
        count: p.object_count + (p.unvetted_count ?? 0), unvetted: !!p.unvetted_only,
      };
      const e = rank(p.ethnicity, needle);
      if (e >= 0) { scored.push([e, { ...base, hit_type: 'ethnicity', matched: p.ethnicity }]); continue; }
      const c = rank(p.country, needle);
      if (c >= 0) { scored.push([10 + c, { ...base, hit_type: 'country', matched: p.country }]); continue; }
      const trad = (p.seed_traditions || []).find((t) => fold(t).includes(needle));
      if (trad) scored.push([20 + rank(trad, needle), { ...base, hit_type: 'tradition', matched: trad }]);
    }
    // "Sami" opens the Sámi before Inari Sami, "Romani" the Romani before
    // Csángó (via country Romania): exact > prefix > word start > anywhere,
    // names before countries before traditions, then the larger collection.
    scored.sort((a, b) => a[0] - b[0] || b[1].count - a[1].count);
    return scored.slice(0, 12).map(([, h]) => h);
  }, [points, q]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center pt-24"
      style={{ background: 'rgba(0,0,0,0.55)' }}
      onClick={() => setOpen(false)}
    >
      <div
        className="sidebar-panel w-full max-w-[520px] rounded-md border border-dusk p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && hits[0]) {
              onSelect(hits[0].key);
              setOpen(false);
              setQ('');
            }
          }}
          placeholder="Search ethnicity, country, or tradition (Esc to close)"
          className="w-full bg-transparent px-1 py-2 text-lg outline-none placeholder:text-parchment/40"
        />
        <div className="mt-2 max-h-[380px] overflow-y-auto">
          {hits.length === 0 && q && (
            <div className="sub-meta py-2 text-sm">No matches.</div>
          )}
          {hits.map((h) => (
            <button
              key={h.key + h.hit_type}
              onClick={() => {
                onSelect(h.key);
                setOpen(false);
                setQ('');
              }}
              className="flex w-full items-baseline justify-between rounded-sm px-2 py-2 text-left hover:bg-dusk/40"
            >
              <div>
                <div className="font-serif text-base">{h.ethnicity}</div>
                <div className="sub-mono text-[10px] uppercase tracking-widest">
                  {h.country}
                  {h.hit_type !== 'ethnicity' && (
                    <span className="ml-2 opacity-70">via {h.hit_type}: {h.matched}</span>
                  )}
                </div>
              </div>
              <div className="sub-mono text-[10px] uppercase tracking-widest">
                {`${h.count} obj`}
              </div>
            </button>
          ))}
          {!q && (
            <div className="sub-meta px-2 py-2 text-sm">
              Try <em>shashmaqam</em>, <em>Bamar</em>, <em>batik</em>, <em>Vietnam</em>…
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
