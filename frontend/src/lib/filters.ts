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
 * latitude to be inside a band.  A latitude bound is applied only when it
 * differs from the default, which is exactly when `filtersToParams` sends it,
 * so the map's count and the server's summary see the same rule.
 */
import type { CatalogColumns } from './catalogTable';

export interface CatalogFilters {
  orbitMin: number;
  orbitMax: number;
  timeMin: number | null;
  timeMax: number | null;
  half: 'all' | 'L' | 'M';
  pixelMaxKm: number | null;
  emissionMax: number | null;
  onPlanetMin: number | null;
  daysideOnly: boolean;
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
  pixelMaxKm: null,
  emissionMax: null,
  onPlanetMin: null,
  daysideOnly: false,
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
  for (let i = 0; i < n; i++) {
    const orbit = columns.orbit[i];
    if (Number.isFinite(orbit) && (orbit < filters.orbitMin || orbit > filters.orbitMax)) continue;
    const t = columns.startTimeMs[i];
    if (Number.isFinite(t)) {
      if (filters.timeMin !== null && t < filters.timeMin) continue;
      if (filters.timeMax !== null && t > filters.timeMax) continue;
    }
    if (wantHalf !== null && columns.half[i].toUpperCase() !== wantHalf) continue;
    const pixel = columns.medianPixelKm[i];
    if (filters.pixelMaxKm !== null && Number.isFinite(pixel) && pixel > filters.pixelMaxKm) continue;
    const emission = columns.boreEmission[i];
    if (filters.emissionMax !== null && Number.isFinite(emission) && emission > filters.emissionMax) continue;
    const onPlanet = columns.onPlanetFrac[i];
    if (filters.onPlanetMin !== null && !(onPlanet >= filters.onPlanetMin)) continue;
    if (filters.daysideOnly && !(columns.daysideFrac[i] > 0)) continue;
    const lat = columns.boreLat[i];
    if (filters.latMin !== DEFAULT_FILTERS.latMin && !(lat >= filters.latMin)) continue;
    if (filters.latMax !== DEFAULT_FILTERS.latMax && !(lat <= filters.latMax)) continue;
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
  if (filters.pixelMaxKm !== null) params.pixel_max_km = String(filters.pixelMaxKm);
  if (filters.emissionMax !== null) params.emission_max = String(filters.emissionMax);
  if (filters.onPlanetMin !== null) params.on_planet_min = String(filters.onPlanetMin);
  if (filters.daysideOnly) params.dayside_only = 'true';
  if (filters.latMin !== DEFAULT_FILTERS.latMin) params.lat_min = String(filters.latMin);
  if (filters.latMax !== DEFAULT_FILTERS.latMax) params.lat_max = String(filters.latMax);
  if (filters.revisitOnly) params.revisit_only = 'true';
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
