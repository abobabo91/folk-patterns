import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useLoader } from '@react-three/fiber';
import { Html, OrbitControls, Stars } from '@react-three/drei';
import * as THREE from 'three';
import type { GlobePoint } from '../lib/types';

export type EarthMode = 'satellite' | 'outlines';

interface Props {
  points: GlobePoint[];
  onSelect: (key: string | null) => void;
  activeKey: string | null;
  theme?: 'dark' | 'light';
  earthMode?: EarthMode;
}

// Country-name normalization so seed country names (which sometimes carry
// annotations like "China (Xinjiang)") match GeoJSON country names.
const COUNTRY_ALIASES: Record<string, string> = {
  'China (Xinjiang)': 'China',
  'Xinjiang': 'China',
  'Myanmar': 'Myanmar',
  'Burma': 'Myanmar',
};

function normalizeCountry(s: string): string {
  return COUNTRY_ALIASES[s] ?? s;
}

// NASA Blue Marble (daytime) — landmasses clearly visible, no clouds. Better
// than the night texture for reading marker positions against continents.
const EARTH_TEXTURE_URL =
  'https://unpkg.com/three-globe@2.34.0/example/img/earth-blue-marble.jpg';
const BUMP_URL =
  'https://unpkg.com/three-globe@2.34.0/example/img/earth-topology.png';

const GLOBE_RADIUS = 1;

function latLonToVec3(lat: number, lon: number, radius: number): THREE.Vector3 {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lon + 180) * (Math.PI / 180);
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

function EarthSphere() {
  const texture = useLoader(THREE.TextureLoader, EARTH_TEXTURE_URL);
  // MeshBasicMaterial is UNLIT — the whole globe shows the texture at full
  // brightness regardless of light direction. This is what you want for a
  // "world atlas" style globe where users see all continents uniformly.
  // Slight color boost so the NASA Blue Marble (which is naturally muted for
  // scientific accuracy) reads brighter for map-atlas use.
  return (
    <mesh>
      <sphereGeometry args={[GLOBE_RADIUS, 96, 96]} />
      <meshBasicMaterial map={texture} color="#c8c8c0" />
    </mesh>
  );
}

// Alternate "outlines" mode: a flat-colored sphere so the country-border
// linework + labels dominate. Colored per theme (parchment in light, ink in
// dark). No texture. Users toggle to this when the satellite view is too busy.
function OutlineSphere({ theme }: { theme: 'dark' | 'light' }) {
  const color = theme === 'light' ? '#eae6d8' : '#141416';
  return (
    <mesh>
      <sphereGeometry args={[GLOBE_RADIUS, 96, 96]} />
      <meshBasicMaterial color={color} />
    </mesh>
  );
}

// GeoJSON polygon ring -> line segments as [x,y,z, x,y,z, ...] on a sphere.
function ringToSegments(ring: number[][], radius: number, out: number[]) {
  for (let i = 0; i < ring.length - 1; i++) {
    const a = latLonToVec3(ring[i][1], ring[i][0], radius);
    const b = latLonToVec3(ring[i + 1][1], ring[i + 1][0], radius);
    out.push(a.x, a.y, a.z, b.x, b.y, b.z);
  }
}

function CountryBorders({ theme, earthMode }: { theme: 'dark' | 'light'; earthMode: EarthMode }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    fetch('/data/world-countries.geojson')
      .then((r) => r.json())
      .then(setData)
      .catch(() => {});
  }, []);

  const geometry = useMemo(() => {
    if (!data) return null;
    const positions: number[] = [];
    const r = GLOBE_RADIUS * 1.002;
    for (const f of data.features) {
      const g = f.geometry;
      if (!g) continue;
      const polygons = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
      for (const polygon of polygons) {
        for (const ring of polygon) {
          ringToSegments(ring, r, positions);
        }
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    return geo;
  }, [data]);

  if (!geometry) return null;
  // Outline mode uses a flat sphere, so borders can be sharper + darker.
  // Satellite mode needs to keep enough transparency to not obscure terrain.
  const color = earthMode === 'outlines'
    ? (theme === 'light' ? '#3a3a3c' : '#e4dec9')
    : (theme === 'light' ? '#1a1a1c' : '#ffffff');
  const opacity = earthMode === 'outlines' ? 0.9 : (theme === 'light' ? 0.7 : 0.75);
  return (
    <lineSegments geometry={geometry}>
      <lineBasicMaterial color={color} transparent opacity={opacity} depthWrite={false} />
    </lineSegments>
  );
}

// Front-facing label — only visible when its 3D position faces the camera.
// Uses useFrame + camera.position dot product against the label's world-space
// normal (the position on the unit sphere), avoiding drei's flaky occlude prop.
function CountryLabel({ name, pos, color, shadow }: {
  name: string;
  pos: THREE.Vector3;
  color: string;
  shadow: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const normal = useMemo(() => pos.clone().normalize(), [pos]);

  useFrame(({ camera }) => {
    if (!ref.current) return;
    // Camera-to-label direction. If they point roughly the same way as the
    // label's outward normal, the label is on the near hemisphere.
    const camDir = camera.position.clone().normalize();
    const dot = camDir.dot(normal);
    // Show label only when its normal aligns with the camera direction — the
    // center of the visible hemisphere, not the edges (which look ugly).
    // Fade smoothly from dot=0.3 (invisible) to dot=0.7 (full).
    const t = Math.max(0, Math.min(1, (dot - 0.3) / 0.4));
    ref.current.style.opacity = String(t);
  });

  return (
    <Html
      position={[pos.x, pos.y, pos.z]}
      center
      // Keep labels below the sidebar (which is z-30). drei's default Html
      // zIndex range is [16777271, 16777000] which sits above everything.
      zIndexRange={[10, 0]}
      style={{
        pointerEvents: 'none',
        color,
        fontFamily: '"Cormorant Garamond", serif',
        fontSize: 13,
        fontWeight: 500,
        whiteSpace: 'nowrap',
        textShadow: shadow,
        letterSpacing: '0.03em',
        userSelect: 'none',
        transition: 'opacity 150ms',
      }}
    >
      <div ref={ref} style={{ opacity: 0 }}>{name}</div>
    </Html>
  );
}

// Bounding-box size in degrees. Used to skip micro-nations that clutter at zoom 1.
function _bboxSize(coords: number[][]): number {
  let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
  for (const c of coords) {
    if (c[0] < minLng) minLng = c[0];
    if (c[0] > maxLng) maxLng = c[0];
    if (c[1] < minLat) minLat = c[1];
    if (c[1] > maxLat) maxLat = c[1];
  }
  return Math.max(maxLng - minLng, maxLat - minLat);
}

function _allCoords(f: any): number[][] {
  const g = f.geometry;
  if (!g) return [];
  const polygons = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
  const out: number[][] = [];
  for (const p of polygons) for (const ring of p) for (const pt of ring) out.push(pt);
  return out;
}

function CountryLabels({ theme, seedCountries }: { theme: 'dark' | 'light'; seedCountries: Set<string> }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    fetch('/data/world-countries.geojson')
      .then((r) => r.json())
      .then(setData)
      .catch(() => {});
  }, []);

  const labels = useMemo(() => {
    if (!data) return [];
    const items: { name: string; pos: THREE.Vector3 }[] = [];
    for (const f of data.features) {
      const rawName = f.properties?.name || '';
      // Only label countries we have data for — clean UX + less clutter.
      if (!seedCountries.has(rawName)) continue;
      const coords = _allCoords(f);
      if (coords.length === 0) continue;
      const avgLng = coords.reduce((s, c) => s + c[0], 0) / coords.length;
      const avgLat = coords.reduce((s, c) => s + c[1], 0) / coords.length;
      const pos = latLonToVec3(avgLat, avgLng, GLOBE_RADIUS * 1.02);
      items.push({ name: rawName, pos });
    }
    return items;
  }, [data, seedCountries]);

  const color = theme === 'light' ? '#1a1a1c' : '#f4efe6';
  const shadow = theme === 'light' ? '0 1px 3px rgba(255,255,255,0.8)' : '0 1px 3px rgba(0,0,0,0.85)';
  return (
    <>
      {labels.map((l, i) => (
        <CountryLabel key={l.name + i} name={l.name} pos={l.pos} color={color} shadow={shadow} />
      ))}
    </>
  );
}

function AutoRotate({ enabled }: { enabled: boolean }) {
  const ref = useRef<THREE.Group>(null);
  useFrame((_state, delta) => {
    if (enabled && ref.current) ref.current.rotation.y += delta * 0.05;
  });
  return <group ref={ref} />;
}

function Markers({
  points,
  onSelect,
  activeKey,
}: Props) {
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const items = useMemo(() => {
    return points.map((p) => {
      const pos = latLonToVec3(p.lat, p.lon, GLOBE_RADIUS * 1.003);
      // Log-scale marker so a 100-object cluster isn't 100x bigger than a 1-object one.
      const count = Math.max(1, p.object_count || 1);
      // sqrt scaling, kept tight: counts run 0–270, and at the old
      // 0.008 + 0.0018·sqrt(n) the 200-object halos were ~4° wide and merged
      // into blobs over Ethiopia and Southeast Asia. Now 0.0065 (n=1) to
      // 0.016 (n=270), halo 1.7x.
      const coreRadius = 0.006 + 0.0006 * Math.sqrt(count);
      return { ...p, pos, coreRadius };
    });
  }, [points]);

  return (
    <>
      {items.map((it) => {
        const active = it.key === activeKey;
        const hovered = it.key === hoveredKey;
        return (
          <group key={it.key} position={it.pos}>
            <mesh
              onPointerDown={(e) => {
                e.stopPropagation();
                onSelect(it.key);
              }}
              onPointerOver={(e) => {
                e.stopPropagation();
                document.body.style.cursor = 'pointer';
                setHoveredKey(it.key);
              }}
              onPointerOut={() => {
                document.body.style.cursor = '';
                setHoveredKey((k) => (k === it.key ? null : k));
              }}
            >
              <sphereGeometry args={[it.coreRadius, 16, 16]} />
              <meshBasicMaterial color={active ? '#f4efe6' : '#e0a94a'} />
            </mesh>
            {/* Soft halo — 1.7x core radius, small opacity, doesn't overwhelm */}
            <mesh>
              <sphereGeometry args={[it.coreRadius * 1.7, 16, 16]} />
              <meshBasicMaterial color="#e0a94a" transparent opacity={0.22} depthWrite={false} />
            </mesh>
            {(hovered || active) && (
              <Html
                position={[0, it.coreRadius * 3, 0]}
                center
                zIndexRange={[10, 0]}
                style={{
                  pointerEvents: 'none',
                  color: '#1a1a1c',
                  background: 'rgba(244, 239, 230, 0.96)',
                  padding: '3px 8px',
                  borderRadius: 3,
                  fontFamily: '"Cormorant Garamond", serif',
                  fontSize: 13,
                  fontWeight: 500,
                  whiteSpace: 'nowrap',
                  boxShadow: '0 2px 8px rgba(0,0,0,0.25)',
                  letterSpacing: '0.02em',
                  userSelect: 'none',
                }}
              >
                <div>
                  {it.ethnicity}
                  <span style={{
                    marginLeft: 6, fontFamily: 'ui-monospace, monospace',
                    fontSize: 10, opacity: 0.6, textTransform: 'uppercase',
                    letterSpacing: '0.1em',
                  }}>
                    {it.object_count} obj
                  </span>
                </div>
              </Html>
            )}
          </group>
        );
      })}
    </>
  );
}

export function ThreeGlobe({ points, onSelect, activeKey, theme = 'dark', earthMode = 'satellite' }: Props) {
  // Set of country names we have data for — used to filter the country labels
  // to only the ones that are meaningful. Uses the seed country name after
  // normalization ("China (Xinjiang)" -> "China", etc.).
  const seedCountrySet = useMemo(
    () => new Set(points.map((p) => normalizeCountry(p.country))),
    [points],
  );

  // Camera position: outside the globe, aligned with the geographic centroid of
  // the current dataset so first render shows markers, not empty ocean. Camera
  // pulled in tighter so the globe fills more of the viewport.
  const startPos = useMemo<[number, number, number]>(() => {
    if (points.length === 0) return [0, 0, 2.1];
    let sx = 0, sy = 0, sz = 0;
    for (const p of points) {
      const v = latLonToVec3(p.lat, p.lon, 1);
      sx += v.x; sy += v.y; sz += v.z;
    }
    const n = points.length;
    const v = new THREE.Vector3(sx / n, sy / n, sz / n).normalize().multiplyScalar(2.1);
    return [v.x, v.y, v.z];
  }, [points]);

  const bg = theme === 'light' ? '#f8f5ee' : '#0a0a0c';
  return (
    <div style={{ position: 'absolute', inset: 0, background: bg }}>
      <Canvas camera={{ position: startPos, fov: 40 }}>
        {/* No lights needed — both sphere modes use MeshBasicMaterial (unlit). */}
        <Suspense fallback={null}>
          {earthMode === 'satellite' ? <EarthSphere /> : <OutlineSphere theme={theme} />}
        </Suspense>
        <CountryBorders theme={theme} earthMode={earthMode} />
        <CountryLabels theme={theme} seedCountries={seedCountrySet} />
        <Markers points={points} onSelect={onSelect} activeKey={activeKey} />
        {theme === 'dark' && earthMode === 'satellite' && (
          <Stars radius={100} depth={50} count={4000} factor={2.5} fade speed={0.4} />
        )}
        <OrbitControls
          enablePan={false}
          enableZoom={true}
          minDistance={1.25}
          maxDistance={5}
          autoRotate={false}
          rotateSpeed={0.6}
        />
      </Canvas>
    </div>
  );
}
