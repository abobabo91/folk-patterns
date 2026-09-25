import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import type { GlobePoint } from '../lib/types';
import 'maplibre-gl/dist/maplibre-gl.css';

interface Props {
  points: GlobePoint[];
  onSelect: (key: string | null) => void;
  activeKey: string | null;
  theme?: 'dark' | 'light';
}

// CARTO hosts both a dark and a light editorial style — swap based on theme.
const STYLE_URL_DARK =
  'https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json';
const STYLE_URL_LIGHT =
  'https://basemaps.cartocdn.com/gl/positron-nolabels-gl-style/style.json';

export function MapLibreGlobe({ points, onSelect, activeKey, theme = 'dark' }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: theme === 'light' ? STYLE_URL_LIGHT : STYLE_URL_DARK,
      // Opens framed on every marker (Morocco to the Philippines, Cape to
      // Kazakhstan) — see fitBounds on load; this is only the first frame.
      center: [55, 15],
      zoom: 2,
      projection: { type: 'globe' } as any,
      pitch: 0,
      attributionControl: false,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    // The CARTO style carries its own "© CARTO, © OpenStreetMap" credit.
    // Compact = an (i) button; MapLibre opens it expanded, so it is closed
    // on load below, where it would otherwise cover the footer.
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-left');
    mapRef.current = map;
    if (typeof window !== 'undefined') (window as any).__map = map;

    map.on('load', () => {
      containerRef.current
        ?.querySelector('.maplibregl-ctrl-attrib')
        ?.classList.remove('maplibregl-compact-show');
      // Add a soft golden glow around each point using a symbol layer.
      const geojson = {
        type: 'FeatureCollection',
        features: points.map((p) => ({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
          properties: {
            key: p.key,
            ethnicity: p.ethnicity,
            country: p.country,
            count: Math.max(1, p.object_count),
          },
        })),
      } as any;
      map.addSource('ethnicities', { type: 'geojson', data: geojson });
      const bounds = new maplibregl.LngLatBounds();
      points.forEach((p) => bounds.extend([p.lon, p.lat]));
      if (!bounds.isEmpty()) {
        const narrow = window.innerWidth < 640;
        map.fitBounds(bounds, {
          padding: narrow ? { top: 90, bottom: 60, left: 20, right: 20 } : { top: 110, bottom: 80, left: 60, right: 60 },
          duration: 0,
        });
      }

      // Glow halo — sqrt-ish stops so small collections stay visible while
      // large ones grow noticeably (Kinh 24 obj should look bigger than Chin 10).
      map.addLayer({
        id: 'eth-glow',
        type: 'circle',
        source: 'ethnicities',
        paint: {
          'circle-radius': [
            'interpolate', ['linear'], ['get', 'count'],
            0, 10, 5, 12, 15, 18, 30, 24, 60, 32, 120, 40,
          ],
          'circle-color': '#e0a94a',
          'circle-opacity': 0.18,
          'circle-blur': 0.6,
        },
      });
      // Core dot
      map.addLayer({
        id: 'eth-dot',
        type: 'circle',
        source: 'ethnicities',
        paint: {
          'circle-radius': [
            'interpolate', ['linear'], ['get', 'count'],
            0, 3.5, 5, 4, 15, 5.5, 30, 7, 60, 9, 120, 11,
          ],
          'circle-color': '#e0a94a',
          'circle-stroke-color': '#0a0a0c',
          'circle-stroke-width': 1.2,
        },
      });
      map.addLayer({
        id: 'eth-label',
        type: 'symbol',
        source: 'ethnicities',
        layout: {
          'text-field': ['get', 'ethnicity'],
          'text-size': 11,
          // Labels never overlap (MapLibre hides a colliding one). Try four
          // sides before giving up, and place the biggest collections first,
          // so the labels that survive at low zoom are the ones worth reading.
          // A marker without a label still names itself on hover.
          'text-variable-anchor': ['top', 'bottom', 'left', 'right'],
          'text-radial-offset': 0.9,
          'text-justify': 'auto',
          'symbol-sort-key': ['-', 0, ['get', 'count']],
          'text-font': ['Noto Sans Regular'],
          'text-letter-spacing': 0.05,
        },
        paint: {
          'text-color': theme === 'light' ? '#1a1a1c' : '#f4efe6',
          'text-halo-color': theme === 'light' ? '#f8f5ee' : '#0a0a0c',
          'text-halo-width': 1.5,
        },
      });

      map.on('click', 'eth-dot', (e) => {
        const f = e.features?.[0];
        if (!f) return;
        onSelect((f.properties as any).key);
        const coords = (f.geometry as any).coordinates as [number, number];
        map.flyTo({ center: coords, zoom: 5, speed: 0.8 });
      });
      const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10, className: 'eth-hover' });
      map.on('mouseenter', 'eth-dot', (e) => {
        map.getCanvas().style.cursor = 'pointer';
        const f = e.features?.[0];
        if (!f) return;
        const pr = f.properties as any;
        hover
          .setLngLat((f.geometry as any).coordinates)
          .setHTML(`<strong>${pr.ethnicity}</strong> <span>${pr.country} · ${pr.count} objects</span>`)
          .addTo(map);
      });
      map.on('mouseleave', 'eth-dot', () => {
        map.getCanvas().style.cursor = '';
        hover.remove();
      });
    });

    return () => {
      map.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);

  // Reflect activeKey change by highlighting the selected feature.
  useEffect(() => {
    if (!mapRef.current || !mapRef.current.isStyleLoaded()) return;
    // (Cheap version) — we already fly to on click. Nothing else needed for MVP.
  }, [activeKey]);

  return <div ref={containerRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />;
}
