import { useCallback, useEffect, useState } from 'react';
import { artFormLabel, imageSrc, regionLabel } from '../lib/catalog';

interface Pt {
  key: string;
  ethnicity: string;
  country: string;
  region: string;
  top_image: string | null;
  object_count: number;
  seed_traditions: string[];
}
interface Tile { key: string; label: string; count: number; cover: string | null }
interface Props {
  points: Pt[];
  categories: Tile[];
  regions: Tile[];
  stats: { cultures: number; images: number; countries: number };
}
interface Featured { point: Pt; title: string; artForm: string; image: string }

const pick = <T,>(xs: T[]): T => xs[Math.floor(Math.random() * xs.length)];
const nf = (n: number) => n.toLocaleString('en-US');

function Tiles({ items, href }: { items: Tile[]; href: (t: Tile) => string }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {items.map((t) => (
        <a key={t.key} href={href(t)} className="card group relative block aspect-[4/3] overflow-hidden">
          {t.cover && <img src={t.cover} alt="" loading="lazy" className="absolute inset-0 h-full w-full object-cover transition duration-300 group-hover:scale-105" />}
          <div className="absolute inset-0 bg-gradient-to-t from-black/75 via-black/10 to-transparent" />
          <div className="absolute bottom-0 left-0 right-0 p-3 text-white">
            <div className="font-serif text-lg leading-tight">{t.label}</div>
            <div className="font-mono text-[10px] uppercase tracking-widest opacity-80">{nf(t.count)}</div>
          </div>
        </a>
      ))}
    </div>
  );
}

export function Home({ points, categories, regions, stats }: Props) {
  const [person, setPerson] = useState<Pt | null>(null);
  const [item, setItem] = useState<Featured | null>(null);
  const [itemLoading, setItemLoading] = useState(false);

  const newPerson = useCallback(() => {
    const pool = points.filter((p) => p.top_image && p.object_count > 0);
    setPerson((cur) => {
      let next = pick(pool);
      while (cur && next.key === cur.key && pool.length > 1) next = pick(pool);
      return next;
    });
  }, [points]);

  // A random picture: a random people, then a random image from its shard.
  const newItem = useCallback(async () => {
    setItemLoading(true);
    const pool = points.filter((p) => p.object_count > 0);
    for (let attempt = 0; attempt < 5; attempt++) {
      const p = pick(pool);
      try {
        const shard = await (await fetch(`/data/ethnicities/${p.key}.json`)).json();
        const all: { title: string; artForm: string; image: string }[] = [];
        for (const [artForm, objs] of Object.entries<any[]>(shard.art_form_buckets || {})) {
          for (const o of objs) if (o.image) all.push({ title: o.title || o.tradition || artFormLabel(artForm), artForm, image: o.image });
        }
        for (const ph of shard.commons_photos || []) if (ph.thumb_url) all.push({ title: ph.title || '', artForm: 'commons', image: ph.thumb_url });
        if (all.length) {
          setItem({ point: p, ...pick(all) });
          break;
        }
      } catch {
        /* try another people */
      }
    }
    setItemLoading(false);
  }, [points]);

  useEffect(() => {
    newPerson();
    newItem();
  }, [newPerson, newItem]);

  return (
    <main className="mx-auto max-w-6xl px-6 pb-16">
      <section className="py-10 sm:py-14">
        <h1 className="font-serif text-4xl font-medium leading-tight sm:text-5xl">
          The world's peoples,<br />and what they made.
        </h1>
        <p className="sub-meta mt-4 max-w-2xl text-lg leading-relaxed">
          An atlas of {nf(stats.cultures)} cultures in {nf(stats.countries)} countries, with {nf(stats.images)} pictures of the
          textiles, masks, jewelry, tools and buildings that museums and Wikimedia Commons hold for them.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <a href="/atlas" className="rounded-full bg-amber-500 px-5 py-2.5 text-[11px] uppercase tracking-widest text-ink">Open the atlas</a>
          <a href="/catalog" className="rounded-full border border-dusk px-5 py-2.5 text-[11px] uppercase tracking-widest">Browse the catalog</a>
        </div>
      </section>

      <section className="grid gap-6 md:grid-cols-2">
        <div className="card flex flex-col">
          <div className="flex items-center justify-between px-5 pt-4">
            <h2 className="font-mono text-[11px] font-normal uppercase tracking-widest text-parchment/60">A people</h2>
            <button onClick={newPerson} className="sub-mono text-[10px] uppercase tracking-widest hover:text-amber-400">Another ↻</button>
          </div>
          {person ? (
            <>
              <a href={`/atlas?culture=${person.key}`} className="mt-3 block aspect-[4/3] overflow-hidden bg-dusk">
                <img src={imageSrc(person.top_image!)} alt={person.ethnicity} className="h-full w-full object-cover" />
              </a>
              <div className="flex flex-1 flex-col p-5">
                <h3 className="font-serif text-3xl leading-tight">{person.ethnicity}</h3>
                <div className="sub-mono mt-1 text-[10px] uppercase tracking-widest">
                  {person.country} · {regionLabel(person.region)} · {nf(person.object_count)} objects
                </div>
                {person.seed_traditions.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {person.seed_traditions.map((t) => <span key={t} className="tag">{t}</span>)}
                  </div>
                )}
                <div className="mt-auto pt-5">
                  <a href={`/atlas?culture=${person.key}`} className="rounded-full bg-amber-500 px-4 py-2 text-[11px] uppercase tracking-widest text-ink">Open on the atlas</a>
                </div>
              </div>
            </>
          ) : <div className="aspect-[4/3] animate-pulse bg-dusk/50" />}
        </div>

        <div className="card flex flex-col">
          <div className="flex items-center justify-between px-5 pt-4">
            <h2 className="font-mono text-[11px] font-normal uppercase tracking-widest text-parchment/60">A picture</h2>
            <button onClick={newItem} disabled={itemLoading} className="sub-mono text-[10px] uppercase tracking-widest hover:text-amber-400">Another ↻</button>
          </div>
          {item ? (
            <>
              <div className="mt-3 flex aspect-[4/3] items-center justify-center overflow-hidden bg-dusk">
                <img src={imageSrc(item.image)} alt={item.title} className="max-h-full max-w-full object-contain" />
              </div>
              <div className="flex flex-1 flex-col p-5">
                <h3 className="font-serif text-2xl leading-snug">{item.title || artFormLabel(item.artForm)}</h3>
                <div className="sub-mono mt-1 text-[10px] uppercase tracking-widest">
                  {item.point.ethnicity} · {item.point.country} · {artFormLabel(item.artForm)}
                </div>
                <div className="mt-auto pt-5">
                  <a href={`/atlas?culture=${item.point.key}`} className="rounded-full bg-amber-500 px-4 py-2 text-[11px] uppercase tracking-widest text-ink">Go to culture</a>
                </div>
              </div>
            </>
          ) : <div className="aspect-[4/3] animate-pulse bg-dusk/50" />}
        </div>
      </section>

      <section className="mt-14">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="font-serif text-2xl">Browse by category</h2>
          <a href="/catalog" className="sub-mono text-[10px] uppercase tracking-widest hover:text-amber-400">Full catalog →</a>
        </div>
        <Tiles items={categories} href={(t) => `/catalog?cat=${t.key}`} />
      </section>

      <section className="mt-14">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="font-serif text-2xl">Browse by region</h2>
          <a href="/catalog?tab=peoples" className="sub-mono text-[10px] uppercase tracking-widest hover:text-amber-400">All peoples →</a>
        </div>
        <Tiles items={regions} href={(t) => `/catalog?tab=peoples&region=${t.key}`} />
      </section>
    </main>
  );
}
