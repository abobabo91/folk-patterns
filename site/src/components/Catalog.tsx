import { useEffect, useMemo, useState } from 'react';
import { ART_FORMS, REGIONS, artFormLabel, imageSrc, regionLabel, type ImageRow } from '../lib/catalog';

interface Pt {
  key: string;
  ethnicity: string;
  country: string;
  region: string;
  top_image: string | null;
  object_count: number;
}
interface Props { points: Pt[] }

type Tab = 'objects' | 'peoples';
const PAGE = 60;
const nf = (n: number) => n.toLocaleString('en-US');

function readQuery() {
  const q = new URLSearchParams(window.location.search);
  return {
    tab: (q.get('tab') === 'peoples' ? 'peoples' : 'objects') as Tab,
    cat: q.get('cat') || '',
    region: q.get('region') || '',
  };
}

export function Catalog({ points }: Props) {
  const [tab, setTab] = useState<Tab>('objects');
  const [cat, setCat] = useState('');
  const [region, setRegion] = useState('');
  const [rows, setRows] = useState<ImageRow[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const [picked, setPicked] = useState<ImageRow | null>(null);
  const [broken, setBroken] = useState(false);

  useEffect(() => {
    const q = readQuery();
    setTab(q.tab);
    setCat(q.cat);
    setRegion(q.region);
  }, []);

  // Keep the address in step with the filters, so a view can be shared and the back button works.
  useEffect(() => {
    const q = new URLSearchParams();
    if (tab === 'peoples') q.set('tab', 'peoples');
    if (cat && tab === 'objects') q.set('cat', cat);
    if (region) q.set('region', region);
    const s = q.toString();
    window.history.replaceState(null, '', s ? `?${s}` : window.location.pathname);
    setShown(PAGE);
  }, [tab, cat, region]);

  useEffect(() => {
    fetch('/data/search-images.json')
      .then((r) => r.json())
      .then(setRows)
      .catch(() => setLoadError(true));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setPicked(null);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const byKey = useMemo(() => new Map(points.map((p) => [p.key, p])), [points]);

  // Images per art form (optionally within a region).
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    if (!rows) return c;
    for (const r of rows) {
      if (region && byKey.get(r[1])?.region !== region) continue;
      c[r[2]] = (c[r[2]] || 0) + 1;
    }
    return c;
  }, [rows, region, byKey]);

  const visible = useMemo(() => {
    if (!rows || tab !== 'objects') return [];
    return rows.filter((r) => (!cat || r[2] === cat) && (!region || byKey.get(r[1])?.region === region));
  }, [rows, tab, cat, region, byKey]);

  const peoplesByRegion = useMemo(() => {
    const groups = Object.keys(REGIONS)
      .filter((k) => !region || k === region)
      .map((k) => ({
        key: k,
        peoples: points.filter((p) => p.region === k).sort((a, b) => b.object_count - a.object_count || a.ethnicity.localeCompare(b.ethnicity)),
      }))
      .filter((g) => g.peoples.length);
    return groups;
  }, [points, region]);

  const pickedPoint = picked ? byKey.get(picked[1]) : null;
  const forms = Object.keys(ART_FORMS).filter((k) => counts[k]);

  const regionSelect = (
    <select
      value={region}
      onChange={(e) => setRegion(e.target.value)}
      className="rounded-full border border-dusk bg-transparent px-3 py-1.5 text-[11px] uppercase tracking-widest text-parchment"
    >
      <option value="">All regions</option>
      {Object.entries(REGIONS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>
  );

  return (
    <main className="mx-auto max-w-6xl px-6 pb-16 pt-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-4xl font-medium">Catalog</h1>
          <p className="sub-meta mt-1">Everything in the collection, by category or by people.</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="mode-toggle flex items-center gap-1 rounded-full border p-0.5">
            {(['objects', 'peoples'] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={'rounded-full px-3 py-1.5 text-[11px] uppercase tracking-widest transition ' + (tab === t ? 'bg-amber-500 text-ink' : 'text-parchment/70 hover:text-parchment')}
              >{t === 'objects' ? 'Pictures' : 'Peoples'}</button>
            ))}
          </div>
          {regionSelect}
        </div>
      </div>

      {tab === 'objects' && (
        <>
          <div className="mt-6 flex flex-wrap gap-2">
            <button
              onClick={() => setCat('')}
              className={'rounded-full border px-3 py-1.5 text-[11px] uppercase tracking-widest transition ' + (!cat ? 'border-amber-500 bg-amber-500 text-ink' : 'border-dusk hover:border-amber-500')}
            >All{rows ? ` · ${nf(Object.values(counts).reduce((a, b) => a + b, 0))}` : ''}</button>
            {forms.map((k) => (
              <button
                key={k}
                onClick={() => setCat(k)}
                className={'rounded-full border px-3 py-1.5 text-[11px] uppercase tracking-widest transition ' + (cat === k ? 'border-amber-500 bg-amber-500 text-ink' : 'border-dusk hover:border-amber-500')}
              >{artFormLabel(k)} · {nf(counts[k])}</button>
            ))}
          </div>

          {loadError && <p className="sub-meta mt-8">The image index could not be loaded.</p>}
          {!rows && !loadError && <p className="sub-meta mt-8">Loading the catalog…</p>}
          {rows && (
            <>
              <p className="sub-mono mt-6 text-[10px] uppercase tracking-widest">{nf(visible.length)} pictures</p>
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                {visible.slice(0, shown).map((r, i) => {
                  const p = byKey.get(r[1]);
                  return (
                    <button
                      key={i}
                      onClick={() => { setPicked(r); setBroken(false); }}
                      className="card group relative block aspect-square overflow-hidden text-left"
                    >
                      <img src={imageSrc(r[4])} alt={r[0]} loading="lazy" className="absolute inset-0 h-full w-full object-cover transition duration-300 group-hover:scale-105" />
                      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/75 to-transparent p-2 pt-8 text-white opacity-0 transition group-hover:opacity-100">
                        <div className="line-clamp-2 text-xs leading-snug">{r[0]}</div>
                        <div className="font-mono text-[9px] uppercase tracking-widest opacity-80">{p?.ethnicity}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
              {visible.length > shown && (
                <div className="mt-8 text-center">
                  <button onClick={() => setShown((s) => s + PAGE * 2)} className="rounded-full border border-dusk px-6 py-2.5 text-[11px] uppercase tracking-widest hover:border-amber-500">
                    Show more · {nf(visible.length - shown)} left
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}

      {tab === 'peoples' && (
        <div className="mt-8 space-y-10">
          {peoplesByRegion.map((g) => (
            <section key={g.key}>
              <h2 className="font-serif text-2xl">{regionLabel(g.key)} <span className="sub-mono font-mono text-[10px] uppercase tracking-widest">{g.peoples.length}</span></h2>
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
                {g.peoples.map((p) => (
                  <a key={p.key} href={`/atlas?culture=${p.key}`} className="card group relative block aspect-[4/5] overflow-hidden bg-dusk">
                    {p.top_image && <img src={imageSrc(p.top_image)} alt="" loading="lazy" className="absolute inset-0 h-full w-full object-cover transition duration-300 group-hover:scale-105" />}
                    <div className="absolute inset-0 bg-gradient-to-t from-black/75 via-transparent to-transparent" />
                    <div className="absolute inset-x-0 bottom-0 p-2.5 text-white">
                      <div className="font-serif text-base leading-tight">{p.ethnicity}</div>
                      <div className="font-mono text-[9px] uppercase tracking-widest opacity-80">{p.country}</div>
                    </div>
                  </a>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {picked && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.8)' }} onClick={() => setPicked(null)}>
          <div className="sidebar-panel flex max-h-full w-full max-w-[900px] flex-col rounded-md border border-dusk p-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex min-h-0 flex-1 items-center justify-center">
              {broken
                ? <div className="sub-meta py-16 text-sm">This image no longer loads from its source.</div>
                : <img src={imageSrc(picked[4])} alt={picked[0]} onError={() => setBroken(true)} className="max-h-[70vh] max-w-full object-contain" />}
            </div>
            <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
              <div>
                <div className="font-serif text-lg leading-snug">{picked[0]}</div>
                <div className="sub-mono text-[10px] uppercase tracking-widest">
                  {pickedPoint ? `${pickedPoint.ethnicity} · ${pickedPoint.country}` : picked[1]} · {artFormLabel(picked[2])}
                  {picked[3] ? ` · ${picked[3]}` : ''}
                </div>
              </div>
              <div className="flex gap-2">
                <a href={`/atlas?culture=${picked[1]}`} className="rounded-full bg-amber-500 px-4 py-2 text-[11px] uppercase tracking-widest text-ink">Go to culture</a>
                <button onClick={() => setPicked(null)} className="rounded-full border border-dusk px-4 py-2 text-[11px] uppercase tracking-widest">Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
