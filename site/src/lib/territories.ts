// A culture's approximate home area (scripts/territories.py): a source polygon
// or a Codex ellipse, one small GeoJSON Feature per culture under
// /data/territories/, fetched on first use and shared by both map views.
const cache = new Map<string, Promise<any | null>>();

export function loadTerritory(key: string): Promise<any | null> {
  if (!cache.has(key)) {
    // A failed fetch is not cached, so the next hover retries it (a 404 while
    // the site is being redeployed would otherwise hide the area until reload).
    const p = fetch(`/data/territories/${key}.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .catch(() => { cache.delete(key); return null; });
    cache.set(key, p);
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
