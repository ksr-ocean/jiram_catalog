/**
 * The catalog filters, client side.
 *
 * The same parameter names the contract's `/api/catalog/summary` takes, so
 * that the map (filtered here, in typed arrays) and the coverage charts
 * (filtered by the server) cannot disagree.  The missing-value rule is the
 * contract's and GUI v1's: thresholds *exclude*, they do not *require*, so a
 * frame whose emission angle the geometry engine never fixed stays on the
 * map instead of vanishing the moment a slider moves.  There are two
 * exceptions, both of which drop rows with a missing value: `on_planet_min`,
 * and the latitude bounds -- a frame whose boresight misses the planet has no
 * latitude to be inside a band.  The three filters the amendment of
 * 2026-09-07 added -- instrument, band, quality tier -- follow the same rule:
 * they are equality or membership tests on a value the row either has or does
 * not, and a row with no tier at all is kept.  A latitude bound is applied only when it
 * differs from the default, which is exactly when `filtersToParams` sends it,
 * so the map's count and the server's summary see the same rule.
 */
import { bandsInclude, qualityPasses, type Instrument, type QualityTier } from './bands';
import type { CatalogColumns } from './catalogTable';

export interface CatalogFilters {
  orbitMin: number;
  orbitMax: number;
  timeMin: number | null;
  timeMax: number | null;
  half: 'all' | 'L' | 'M';
  /** Amendment 2026-09-07: which instrument's rows to draw. */
  instrument: 'all' | Instrument;
  /** A band name the row must carry (`M`, `RED`, ...), or nothing. */
  band: string | null;
  /** The worst quality tier still shown; the contract's default is `B`. */
  qualityMin: QualityTier;
  pixelMaxKm: number | null;
  emissionMax: number | null;
  onPlanetMin: number | null;
  daysideOnly: boolean;
  latitudeMode: 'coverage' | 'boresight';
  latMin: number;
  latMax: number;
  revisitOnly: boolean;
}

export const DEFAULT_FILTERS: CatalogFilters = {
  orbitMin: 1,
  orbitMax: 99,
  timeMin: null,
  timeMax: null,
  half: 'all',
  instrument: 'all',
  band: null,
  // `B` rather than `C`: the contract hides tier C unless it is asked for, so
  // the map and the server's summary start out counting the same rows.
  qualityMin: 'B',
  pixelMaxKm: null,
  emissionMax: null,
  onPlanetMin: null,
  daysideOnly: false,
  latitudeMode: 'coverage',
  latMin: -90,
  latMax: 90,
  revisitOnly: false,
};

/** True when nothing in `filters` differs from the defaults. */
export function isDefaultFilters(filters: CatalogFilters): boolean {
  return (Object.keys(DEFAULT_FILTERS) as (keyof CatalogFilters)[]).every(
    (key) => filters[key] === DEFAULT_FILTERS[key],
  );
}

/** Row indices that pass `filters`, as a compact `Uint32Array`. */
export function filterIndices(columns: CatalogColumns, filters: CatalogFilters): Uint32Array {
  const n = columns.n;
  const keep = new Uint32Array(n);
  let count = 0;
  const wantHalf = filters.half === 'all' ? null : filters.half;
  const wantInstrument = filters.instrument === 'all' ? null : filters.instrument.toUpperCase();
  const wantBand = filters.band ? filters.band : null;
  for (let i = 0; i < n; i++) {
    const orbit = columns.orbit[i];
    if (Number.isFinite(orbit) && (orbit < filters.orbitMin || orbit > filters.orbitMax)) continue;
    const t = columns.startTimeMs[i];
    if (Number.isFinite(t)) {
      if (filters.timeMin !== null && t < filters.timeMin) continue;
      if (filters.timeMax !== null && t > filters.timeMax) continue;
    }
    if (wantHalf !== null && columns.half[i].toUpperCase() !== wantHalf) continue;
    if (wantInstrument !== null && columns.instrument[i].toUpperCase() !== wantInstrument) continue;
    if (wantBand !== null && !bandsInclude(columns.bands[i], wantBand)) continue;
    if (!qualityPasses(columns.qualityTier[i], filters.qualityMin)) continue;
    const pixel = columns.medianPixelKm[i];
    if (filters.pixelMaxKm !== null && Number.isFinite(pixel) && pixel > filters.pixelMaxKm)
      continue;
    const emission = columns.boreEmission[i];
    if (filters.emissionMax !== null && Number.isFinite(emission) && emission > filters.emissionMax)
      continue;
    const onPlanet = columns.onPlanetFrac[i];
    if (filters.onPlanetMin !== null && !(onPlanet >= filters.onPlanetMin)) continue;
    if (filters.daysideOnly && !(columns.daysideFrac[i] > 0)) continue;
    const lat = columns.boreLat[i];
    const low =
      filters.latitudeMode !== 'boresight' && Number.isFinite(columns.minLat[i])
        ? columns.minLat[i]
        : lat;
    const high =
      filters.latitudeMode !== 'boresight' && Number.isFinite(columns.maxLat[i])
        ? columns.maxLat[i]
        : lat;
    if (filters.latMin !== DEFAULT_FILTERS.latMin && !(high >= filters.latMin)) continue;
    if (filters.latMax !== DEFAULT_FILTERS.latMax && !(low <= filters.latMax)) continue;
    if (filters.revisitOnly && !columns.hasPartner[i]) continue;
    keep[count++] = i;
  }
  return keep.subarray(0, count);
}

/** The filters as `/api/catalog/summary` query parameters (unset ones omitted). */
export function filtersToParams(filters: CatalogFilters): Record<string, string> {
  const params: Record<string, string> = {};
  if (filters.orbitMin !== DEFAULT_FILTERS.orbitMin) params.orbit_min = String(filters.orbitMin);
  if (filters.orbitMax !== DEFAULT_FILTERS.orbitMax) params.orbit_max = String(filters.orbitMax);
  if (filters.timeMin !== null) params.time_min = new Date(filters.timeMin).toISOString();
  if (filters.timeMax !== null) params.time_max = new Date(filters.timeMax).toISOString();
  if (filters.half !== 'all') params.half = filters.half;
  if (filters.instrument !== 'all') params.instrument = filters.instrument;
  if (filters.band) params.bands = filters.band;
  if (filters.qualityMin !== DEFAULT_FILTERS.qualityMin) params.quality_min = filters.qualityMin;
  if (filters.pixelMaxKm !== null) params.pixel_max_km = String(filters.pixelMaxKm);
  if (filters.emissionMax !== null) params.emission_max = String(filters.emissionMax);
  if (filters.onPlanetMin !== null) params.on_planet_min = String(filters.onPlanetMin);
  if (filters.daysideOnly) params.dayside_only = 'true';
  if (filters.latMin !== DEFAULT_FILTERS.latMin) params.lat_min = String(filters.latMin);
  if (filters.latMax !== DEFAULT_FILTERS.latMax) params.lat_max = String(filters.latMax);
  if (filters.revisitOnly) params.revisit_only = 'true';
  if (filters.latitudeMode === 'boresight') params.latitude_mode = 'boresight';
  return params;
}

/** Indices whose projected position falls inside a screen-space polygon. */
export function indicesInPolygon(
  xy: Float32Array,
  candidates: Uint32Array,
  polygon: [number, number][],
): number[] {
  if (polygon.length < 3) return [];
  let xMin = Infinity;
  let xMax = -Infinity;
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const [x, y] of polygon) {
    if (x < xMin) xMin = x;
    if (x > xMax) xMax = x;
    if (y < yMin) yMin = y;
    if (y > yMax) yMax = y;
  }
  const hits: number[] = [];
  for (let k = 0; k < candidates.length; k++) {
    const i = candidates[k];
    const px = xy[2 * i];
    const py = xy[2 * i + 1];
    if (!Number.isFinite(px) || !Number.isFinite(py)) continue;
    if (px < xMin || px > xMax || py < yMin || py > yMax) continue;
    if (pointInPolygon(px, py, polygon)) hits.push(i);
  }
  return hits;
}

/** Even-odd ray crossing test. */
export function pointInPolygon(x: number, y: number, polygon: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    const intersects = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

/** A closed rectangle as a polygon, for the box-select tool. */
export function boxPolygon(x0: number, y0: number, x1: number, y1: number): [number, number][] {
  return [
    [Math.min(x0, x1), Math.min(y0, y1)],
    [Math.max(x0, x1), Math.min(y0, y1)],
    [Math.max(x0, x1), Math.max(y0, y1)],
    [Math.min(x0, x1), Math.max(y0, y1)],
  ];
}

/** Reproducible starting points; only JIRAM currently has assessed revisits. */
export const CATALOG_PRESETS: { id: string; label: string; filters: Partial<CatalogFilters> }[] = [
  {
    id: 'repeat',
    label: 'Repeat cloud views',
    filters: { instrument: 'JIRAM', half: 'M', revisitOnly: true },
  },
  { id: 'polar', label: 'Polar morphology', filters: { latMin: 60, latitudeMode: 'coverage' } },
  { id: 'texture', label: 'Single-pass texture', filters: { emissionMax: 70, onPlanetMin: 0.5 } },
  {
    id: 'context',
    label: 'Cross-instrument context',
    filters: { instrument: 'all', latitudeMode: 'coverage' },
  },
];

/** Polygon intersection including crossed edges and containment, for region selection. */
export function polygonsIntersect(a: [number, number][], b: [number, number][]): boolean {
  if (a.length < 3 || b.length < 3) return false;
  if (a.some(([x, y]) => pointInPolygon(x, y, b)) || b.some(([x, y]) => pointInPolygon(x, y, a)))
    return true;
  const cross = (p: number[], q: number[], r: number[]) =>
    (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  for (let i = 0; i < a.length; i++)
    for (let j = 0; j < b.length; j++) {
      const p = a[i],
        q = a[(i + 1) % a.length],
        r = b[j],
        s = b[(j + 1) % b.length];
      if (
        Math.max(p[0], q[0]) < Math.min(r[0], s[0]) ||
        Math.max(r[0], s[0]) < Math.min(p[0], q[0]) ||
        Math.max(p[1], q[1]) < Math.min(r[1], s[1]) ||
        Math.max(r[1], s[1]) < Math.min(p[1], q[1])
      )
        continue;
      if (cross(p, q, r) * cross(p, q, s) <= 0 && cross(r, s, p) * cross(r, s, q) <= 0) return true;
    }
  return false;
}
