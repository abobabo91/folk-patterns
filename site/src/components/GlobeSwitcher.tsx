import { useEffect, useMemo, useState } from 'react';
import type { GlobePoint, EthnicityShard } from '../lib/types';
import { MapLibreGlobe } from './MapLibreGlobe';
import { ThreeGlobe, type EarthMode } from './ThreeGlobe';
import { EthnicityPanel } from './EthnicityPanel';
import { SearchOverlay } from './SearchOverlay';
import { ThemeToggle } from './ThemeToggle';
import { getTheme, type Theme } from '../lib/theme';

type Mode = 'atlas' | 'earth';

interface Props {
  points: GlobePoint[];
}

export function GlobeSwitcher({ points }: Props) {
  const [mode, setMode] = useState<Mode>('atlas');
  const [earthMode, setEarthMode] = useState<EarthMode>('satellite');
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [shard, setShard] = useState<EthnicityShard | null>(null);
  const [theme, setTheme] = useState<Theme>('dark');

  useEffect(() => {
    setTheme(getTheme());
    // Listen for theme changes via mutation observer on <html data-theme>.
    const obs = new MutationObserver(() => setTheme(getTheme()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
  }, []);

  // Dev helper: window.__folkPatterns.selectByEthnicity('Bamar') opens the
  // sidebar for testing without needing to raycast-click the 3D globe.
  useEffect(() => {
    (window as any).__folkPatterns = {
      setActiveKey,
      points,
      selectByEthnicity(name: string) {
        const p = points.find((p) => p.ethnicity.toLowerCase().includes(name.toLowerCase()));
        if (p) setActiveKey(p.key);
        return p ?? null;
      },
    };
  }, [points]);
  const activePoint = useMemo(
    () => points.find((p) => p.key === activeKey) || null,
    [points, activeKey],
  );

  useEffect(() => {
    if (!activeKey) {
      setShard(null);
      return;
    }
    let cancelled = false;
    fetch(`/data/ethnicities/${activeKey}.json`)
      .then((r) => r.json())
      .then((s) => {
        if (!cancelled) setShard(s);
      });
    return () => {
      cancelled = true;
    };
  }, [activeKey]);

  return (
    <div className="relative h-screen w-screen overflow-hidden">
      {/* Header + toggle */}
      <div className="pointer-events-none absolute top-0 left-0 right-0 z-20 flex items-start justify-between p-6">
        <div className="pointer-events-auto">
          <h1 className="font-serif text-2xl font-medium tracking-tight">folk-patterns</h1>
          <p className="header-sub mt-1 font-mono text-[10px] uppercase tracking-widest">
            a visual atlas of world folk culture
          </p>
        </div>
        {/* The side panel (max 520px, right edge) would cover these; on wide
            screens they move left of it while it is open. */}
        <div className={'pointer-events-auto flex items-center gap-3 transition-[margin] duration-300 ' + (activeKey ? 'lg:mr-[520px]' : '')}>
          <div className="mode-toggle flex items-center gap-1 rounded-full border border-dusk bg-night/80 p-1 backdrop-blur">
            {(['atlas', 'earth'] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={
                  'px-3 py-1.5 text-[11px] uppercase tracking-widest rounded-full transition ' +
                  (mode === m
                    ? 'bg-amber-500 text-ink'
                    : 'text-parchment/70 hover:text-parchment')
                }
              >
                {m === 'atlas' ? 'Atlas' : 'Earth'}
              </button>
            ))}
          </div>
          {/* Earth submode toggle — only relevant when we're on the 3D globe. */}
          {mode === 'earth' && (
            <div className="mode-toggle flex items-center gap-1 rounded-full border border-dusk bg-night/80 p-1 backdrop-blur">
              {(['satellite', 'outlines'] as EarthMode[]).map((em) => (
                <button
                  key={em}
                  onClick={() => setEarthMode(em)}
                  className={
                    'px-3 py-1.5 text-[11px] uppercase tracking-widest rounded-full transition ' +
                    (earthMode === em
                      ? 'bg-amber-500 text-ink'
                      : 'text-parchment/70 hover:text-parchment')
                  }
                >
                  {em === 'satellite' ? 'Satellite' : 'Outlines'}
                </button>
              ))}
            </div>
          )}
          <ThemeToggle />
        </div>
      </div>

      {/* Globe */}
      <div className="absolute inset-0">
        {mode === 'atlas' ? (
          <MapLibreGlobe points={points} onSelect={setActiveKey} activeKey={activeKey} theme={theme} />
        ) : (
          <ThreeGlobe points={points} onSelect={setActiveKey} activeKey={activeKey} theme={theme} earthMode={earthMode} />
        )}
      </div>

      {/* Side panel */}
      <EthnicityPanel point={activePoint} shard={shard} onClose={() => setActiveKey(null)} />

      {/* ⌘K / Ctrl+K search overlay */}
      <SearchOverlay points={points} onSelect={setActiveKey} />

      {/* Legend footer */}
      {/* Sits clear of the map's corner controls (compact attribution at
          bottom-left, zoom at bottom-right); the key hint is desktop-only. */}
      <div className="footer-legend pointer-events-none absolute bottom-0 left-12 right-14 z-20 flex items-center justify-between gap-4 py-4 sm:py-6 text-[10px] font-mono uppercase tracking-widest">
        <span>{points.length} cultures · {points.reduce((s, p) => s + p.object_count, 0)} objects</span>
        <span className="hidden sm:inline">
          press <kbd className="rounded border border-current px-1 py-0.5">/</kbd> or <kbd className="rounded border border-current px-1 py-0.5">⌘K</kbd> to search · click a marker · <a href="/contribute" className="pointer-events-auto hover:text-amber-400 transition">contribute</a>
        </span>
      </div>
    </div>
  );
}
