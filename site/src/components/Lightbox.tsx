import { createContext, useContext, useEffect, useState } from 'react';
import type { MouseEvent } from 'react';

export interface LightboxItem {
  key: string;
  image: string;
  title: string;
  label: string;          // the category, e.g. "Textile"
  unreviewed: boolean;
  site: string | null;    // the holding museum's own page
  details: string | null; // our object page, for reviewed objects
}

// Set by the culture panel: opens the lightbox on the tile with this key.
export const LightboxContext = createContext<((key: string) => void) | null>(null);

// A plain left click on a tile opens the lightbox; ctrl/cmd/shift/middle
// clicks keep the link's own behaviour (open in a new tab).
export function useLightboxOpen() {
  return useContext(LightboxContext);
}

export function lightboxClick(open: ((key: string) => void) | null, key: string) {
  return (e: MouseEvent) => {
    if (!open || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    open(key);
  };
}

interface Props {
  items: LightboxItem[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
}

export function Lightbox({ items, index, onIndex, onClose }: Props) {
  const item = items[index];
  const [broken, setBroken] = useState(false);
  const go = (d: number) => onIndex((index + d + items.length) % items.length);

  useEffect(() => setBroken(false), [index]);

  // Capture phase, so Escape closes only the lightbox and not the panel under it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowRight') go(1);
      else if (e.key === 'ArrowLeft') go(-1);
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  });

  if (!item) return null;
  const btn = 'lightbox-btn rounded-full border border-white/25 bg-black/50 text-white hover:border-amber-400 hover:text-amber-400 transition';
  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/90 p-4 sm:p-10"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <button onClick={onClose} className={`${btn} absolute right-4 top-4 px-3 py-1.5 text-sm`} aria-label="Close (Esc)" title="Close (Esc)">✕</button>
      {items.length > 1 && (
        <>
          <button onClick={(e) => { e.stopPropagation(); go(-1); }} className={`${btn} absolute left-2 top-1/2 -translate-y-1/2 px-3 py-2 text-xl sm:left-4`} aria-label="Previous (←)">‹</button>
          <button onClick={(e) => { e.stopPropagation(); go(1); }} className={`${btn} absolute right-2 top-1/2 -translate-y-1/2 px-3 py-2 text-xl sm:right-4`} aria-label="Next (→)">›</button>
        </>
      )}
      {broken ? (
        <div className="font-mono text-sm text-white/60">Image unavailable</div>
      ) : (
        <img
          key={item.key}
          src={item.image}
          alt={item.title}
          onClick={(e) => e.stopPropagation()}
          onError={() => setBroken(true)}
          className="max-h-[72vh] max-w-[88vw] object-contain"
        />
      )}
      <div className="mt-4 max-w-3xl text-center text-white" onClick={(e) => e.stopPropagation()}>
        <div className="font-mono text-[10px] uppercase tracking-widest text-white/50">
          {item.label}{item.unreviewed && <span className="text-amber-400"> · unreviewed</span>} · {index + 1} / {items.length}
        </div>
        <div className="mt-1 line-clamp-3 font-serif text-lg leading-snug">{item.title}</div>
        <div className="mt-3 flex justify-center gap-3 font-mono text-[11px] uppercase tracking-widest">
          {item.site && (
            <a href={item.site} target="_blank" rel="noopener noreferrer" className={`${btn} px-3 py-1.5`}>Go to site ↗</a>
          )}
          {item.details && <a href={item.details} className={`${btn} px-3 py-1.5`}>Details</a>}
        </div>
      </div>
    </div>
  );
}
