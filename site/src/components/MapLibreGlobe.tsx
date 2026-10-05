import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import type { GlobePoint } from '../lib/types';
import { loadTerritory } from '../lib/territories';
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

const EMPTY = { type: 'FeatureCollection', features: [] } as any;

export function MapLibreGlobe({ points, onSelect, activeKey, theme = 'dark' }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  // Which culture each territory layer should show; a slow fetch for a culture
  // the pointer has already left is dropped.
  const shown = useRef<Record<string, string | null>>({ 'terr-active': null, 'terr-hover': null });
  const showTerritory = (layer: 'terr-active' | 'terr-hover', key: string | null) => {
    shown.current[layer] = key;
    const src = mapRef.current?.getSource(layer) as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    if (!key) return src.setData(EMPTY);
    loadTerritory(key).then((f) => {
      if (shown.current[layer] === key) src.setData(f ?? EMPTY);
    });
  };

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
    map.addControl(new maplibregl.AttributionControl({
      compact: true,
      customAttribution: 'Areas: Asher &amp; Moseley 2007 via Glottography (CC BY 4.0), Native Land Digital, GREG; others approximate',
    }), 'bottom-left');
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
            unvetted: !!p.unvetted_only,
          },
        })),
      } as any;
      map.addSource('ethnicities', { type: 'geojson', data: geojson, promoteId: 'key' });
      const bounds = new maplibregl.LngLatBounds();
      points.forEach((p) => bounds.extend([p.lon, p.lat]));
      if (!bounds.isEmpty()) {
        const narrow = window.innerWidth < 640;
        map.fitBounds(bounds, {
          padding: narrow ? { top: 90, bottom: 60, left: 20, right: 20 } : { top: 110, bottom: 80, left: 60, right: 60 },
          duration: 0,
        });
      }

      // Home areas sit under the markers: faint for the hovered culture,
      // stronger for the selected one.
      for (const [id, fill, line] of [['terr-hover', 0.1, 0.35], ['terr-active', 0.2, 0.75]] as const) {
        map.addSource(id, { type: 'geojson', data: EMPTY });
        map.addLayer({ id: `${id}-fill`, type: 'fill', source: id, paint: { 'fill-color': '#e0a94a', 'fill-opacity': fill } });
        map.addLayer({ id: `${id}-line`, type: 'line', source: id,
          paint: { 'line-color': '#e0a94a', 'line-opacity': line, 'line-width': 1 } });
      }
      showTerritory('terr-active', shown.current['terr-active']);

      // Glow halo — sqrt-ish stops so small collections stay visible while
      // large ones grow noticeably (Kinh 24 obj should look bigger than Chin 10).
      map.addLayer({
        id: 'eth-glow',
        type: 'circle',
        source: 'ethnicities',
        paint: {
          'circle-radius': [
            'case', ['get', 'unvetted'], 3,
            ['interpolate', ['linear'], ['get', 'count'],
            0, 10, 5, 12, 15, 18, 30, 24, 60, 32, 120, 40,
            ],
          ],
          'circle-color': '#e0a94a',
          'circle-opacity': ['case', ['get', 'unvetted'], 0.08, 0.18],
          'circle-blur': 0.6,
        },
      });
      // Core dot
      map.addLayer({
        id: 'eth-dot',
        type: 'circle',
        source: 'ethnicities',
        paint: {
          // A hovered marker grows and turns opaque, so the one under the
          // pointer is the one a click selects.
          'circle-radius': ['*',
            ['case', ['boolean', ['feature-state', 'hover'], false], 1.8, 1],
            ['case', ['get', 'unvetted'], 2.5,
              ['interpolate', ['linear'], ['get', 'count'],
              0, 3.5, 5, 4, 15, 5.5, 30, 7, 60, 9, 120, 11,
              ],
            ],
          ],
          'circle-color': '#e0a94a',
          'circle-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 1,
            ['get', 'unvetted'], 0.4, 1],
          'circle-stroke-color': '#0a0a0c',
          'circle-stroke-width': 1.2,
        },
      });
      // Invisible hit area: unreviewed dots are 2.5 px, too small to aim at.
      map.addLayer({
        id: 'eth-hit',
        type: 'circle',
        source: 'ethnicities',
        paint: { 'circle-radius': 9, 'circle-opacity': 0 },
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

      map.on('click', 'eth-hit', (e) => {
        const f = e.features?.[0];
        if (!f) return;
        onSelect((f.properties as any).key);
        const coords = (f.geometry as any).coordinates as [number, number];
        map.flyTo({ center: coords, zoom: 5, speed: 0.8 });
      });
      const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10, className: 'eth-hover' });
      let hovered: string | null = null;
      const setHover = (key: string | null) => {
        if (hovered === key) return;
        if (hovered) map.setFeatureState({ source: 'ethnicities', id: hovered }, { hover: false });
        hovered = key;
        if (key) map.setFeatureState({ source: 'ethnicities', id: key }, { hover: true });
        showTerritory('terr-hover', key);
      };
      map.on('mousemove', 'eth-hit', (e) => {
        map.getCanvas().style.cursor = 'pointer';
        const f = e.features?.[0];
        if (!f) return;
        const pr = f.properties as any;
        if (hovered === pr.key) return;
        setHover(pr.key);
        hover
          .setLngLat((f.geometry as any).coordinates)
          .setHTML(`<strong>${pr.ethnicity}</strong> <span>${pr.country} · ${pr.unvetted ? 'unreviewed' : `${pr.count} objects`}</span>`)
          .addTo(map);
      });
      map.on('mouseleave', 'eth-hit', () => {
        map.getCanvas().style.cursor = '';
        setHover(null);
        hover.remove();
      });
    });

    return () => {
      map.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);

  // The selected culture's home area; before the map has loaded this only
  // records the key, and the load handler draws it.
  useEffect(() => {
    showTerritory('terr-active', activeKey);
  }, [activeKey]);

  return <div ref={containerRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />;
}
