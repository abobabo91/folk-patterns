// A culture's approximate home area (scripts/territories.py): a source polygon
// or a Codex ellipse, one small GeoJSON Feature per culture under
// /data/territories/, fetched on first use and shared by both map views.
const cache = new Map<string, Promise<any | null>>();

export function loadTerritory(key: string): Promise<any | null> {
  if (!cache.has(key)) {
    cache.set(key, fetch(`/data/territories/${key}.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  return cache.get(key)!;
}

/** Outer and inner rings of a Polygon or MultiPolygon feature, as [lon, lat] lists. */
export function territoryRings(feature: any): number[][][] {
  const g = feature?.geometry;
  if (!g) return [];
  const polygons = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
  return polygons.flat();
}
