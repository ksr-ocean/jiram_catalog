/**
 * JunoCam footprints: an outline in (east longitude, latitude) turned into
 * polygons the catalog map can draw.
 *
 * A JIRAM row is a boresight, so it is a point.  A JunoCam image is a swath
 * tens of degrees across, and drawing it as its centre would be a lie about
 * what the instrument saw -- so `frames.arrow` sends up to 64 vertices of the
 * on-planet outline and the browser draws the outline itself.
 *
 * Two things make that more than a `map`.  The first is the seam: a footprint
 * that straddles 0/360 is one shape on the sphere and two on a cylindrical
 * map, and drawing it as one polygon paints a band right across the map.  The
 * outline is therefore cut at the seam, with the crossing latitude
 * interpolated so the two pieces meet the edges at the same place.  The second
 * is the pole: an outline that encircles a pole never closes in cylindrical
 * coordinates at all, so its winding is measured and, when it is a full turn,
 * the polygon is closed over the top (or bottom) edge of the map.  In the two
 * polar projections neither problem exists -- the seam is a radius and the
 * pole is the origin -- so there the vertices are simply projected.
 */
import { projectPolar, wrapLon, type ViewMode } from './projection';
import type { CatalogColumns } from './catalogTable';

export type Ring = [number, number][];

export interface FootprintItem {
  /** The catalog row this outline came from, for hover and click. */
  row: number;
  polygon: Ring;
  /** `[xMin, xMax, yMin, yMax]`, so a CPU hover test can reject fast. */
  bounds: [number, number, number, number];
}

export interface FootprintSet {
  items: FootprintItem[];
  /** Rows that contributed an outline; a seam-split row still counts once. */
  n: number;
}

export const EMPTY_FOOTPRINTS: FootprintSet = { items: [], n: 0 };

/** A longitude difference folded into `(-180, 180]`: the way round the ring. */
export function shortestDelta(delta: number): number {
  if (!Number.isFinite(delta)) return 0;
  let d = delta % 360;
  if (d > 180) d -= 360;
  if (d <= -180) d += 360;
  return d;
}

/** A piece's longitudes brought back into `[0, 360]` as a whole. */
function toWrapped(piece: Ring): Ring {
  let low = Infinity;
  let high = -Infinity;
  for (const [x] of piece) {
    if (x < low) low = x;
    if (x > high) high = x;
  }
  const turns = Math.floor((low + high) / 2 / 360);
  return piece.map(([x, y]) => [Math.min(360, Math.max(0, x - turns * 360)), y] as [number, number]);
}

/**
 * Split a closed outline at the 0/360 seam.
 *
 * Returns one polygon when the outline does not cross it, two (or more) when
 * it does, and one polygon closed over the pole when the outline goes right
 * round it.  Fewer than three usable vertices give nothing rather than a
 * degenerate shape.
 */
export function splitSeam(lon: ArrayLike<number>, lat: ArrayLike<number>): Ring[] {
  const xs: number[] = [];
  const ys: number[] = [];
  const n = Math.min(lon.length, lat.length);
  for (let i = 0; i < n; i++) {
    const x = wrapLon(lon[i]);
    const y = lat[i];
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    if (xs.length > 0 && xs[xs.length - 1] === x && ys[ys.length - 1] === y) continue;
    xs.push(x);
    ys.push(y);
  }
  // An outline that repeats its first vertex last is already closed.
  if (xs.length > 1 && xs[0] === xs[xs.length - 1] && ys[0] === ys[ys.length - 1]) {
    xs.pop();
    ys.pop();
  }
  const m = xs.length;
  if (m < 3) return [];

  // Longitudes unwrapped along the closed ring; `u[m]` closes it, so
  // `u[m] - u[0]` is the winding: zero for an ordinary outline, a full turn
  // for one that encircles a pole.
  const u = new Float64Array(m + 1);
  u[0] = xs[0];
  for (let i = 1; i <= m; i++) {
    u[i] = u[i - 1] + shortestDelta(xs[i % m] - xs[(i - 1) % m]);
  }
  const winding = u[m] - u[0];

  const pieces: Ring[] = [];
  let current: Ring = [[u[0], ys[0]]];
  for (let i = 1; i <= m; i++) {
    const x0 = u[i - 1];
    const y0 = ys[(i - 1) % m];
    const x1 = u[i];
    const y1 = ys[i % m];
    const low = Math.min(x0, x1);
    const high = Math.max(x0, x1);
    const crossings: number[] = [];
    for (let k = Math.floor(low / 360) + 1; k <= Math.ceil(high / 360) - 1; k++) crossings.push(k);
    if (x1 < x0) crossings.reverse();
    for (const k of crossings) {
      const boundary = k * 360;
      const t = (boundary - x0) / (x1 - x0);
      const y = y0 + t * (y1 - y0);
      current.push([boundary, y]);
      pieces.push(current);
      current = [[boundary, y]];
    }
    current.push([x1, y1]);
  }
  pieces.push(current);

  const wrapped = pieces.map(toWrapped);
  // The last piece ends where the first begins -- they are one piece cut by
  // the start of the vertex list, not by the seam.
  if (wrapped.length > 1) {
    const last = wrapped.pop() as Ring;
    wrapped[0] = last.concat(wrapped[0].slice(1));
  }

  if (Math.abs(winding) > 180) {
    // A full turn: the outline encircles a pole, so it runs from one edge of
    // the map to the other and is closed over the pole itself.
    if (wrapped.length !== 1) return wrapped.filter((piece) => piece.length >= 3);
    const piece = wrapped[0];
    let sum = 0;
    for (const [, y] of piece) sum += y;
    const pole = sum >= 0 ? 90 : -90;
    const start = piece[0][0];
    const end = piece[piece.length - 1][0];
    return [[...piece, [end, pole] as [number, number], [start, pole] as [number, number]]];
  }
  return wrapped.filter((piece) => piece.length >= 3);
}

/** The outline as polygons in whichever view mode is current. */
export function footprintPolygons(
  lon: ArrayLike<number>,
  lat: ArrayLike<number>,
  mode: ViewMode,
): Ring[] {
  if (mode === 'cyl') return splitSeam(lon, lat);
  const ring: Ring = [];
  let inHemisphere = false;
  const n = Math.min(lon.length, lat.length);
  for (let i = 0; i < n; i++) {
    const y = lat[i];
    const x = lon[i];
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    if (mode === 'N' ? y >= 0 : y <= 0) inHemisphere = true;
    // Clamp at the equator rather than dropping the vertex: an outline that
    // straddles it keeps its shape up to the rim instead of falling apart.
    const clamped = mode === 'N' ? Math.max(0, y) : Math.min(0, y);
    ring.push(projectPolar(clamped, x, mode));
  }
  return inHemisphere && ring.length >= 3 ? [ring] : [];
}

function boundsOf(ring: Ring): [number, number, number, number] {
  let xMin = Infinity;
  let xMax = -Infinity;
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const [x, y] of ring) {
    if (x < xMin) xMin = x;
    if (x > xMax) xMax = x;
    if (y < yMin) yMin = y;
    if (y > yMax) yMax = y;
  }
  return [xMin, xMax, yMin, yMax];
}

/**
 * The outlines of every filtered row that has one.
 *
 * `limit` is a guard rather than a policy: a footprint is two orders of
 * magnitude more geometry than a point, and the map must still repaint in a
 * frame when a filter change puts every JunoCam image on screen at once.
 */
export const FOOTPRINT_LIMIT = 4000;

export function computeFootprints(
  columns: CatalogColumns,
  filtered: Uint32Array,
  mode: ViewMode,
  limit: number = FOOTPRINT_LIMIT,
): FootprintSet {
  const { fpLon, fpLat } = columns;
  if (!fpLon || fpLon.values.length === 0) return EMPTY_FOOTPRINTS;
  const items: FootprintItem[] = [];
  let rows = 0;
  for (let k = 0; k < filtered.length && rows < limit; k++) {
    const i = filtered[k];
    const lonStart = fpLon.offsets[i];
    const lonEnd = fpLon.offsets[i + 1];
    if (lonEnd - lonStart < 3) continue;
    const latStart = fpLat.offsets[i];
    const latEnd = fpLat.offsets[i + 1];
    const rings = footprintPolygons(
      fpLon.values.subarray(lonStart, lonEnd),
      fpLat.values.subarray(latStart, latEnd),
      mode,
    );
    if (rings.length === 0) continue;
    rows += 1;
    for (const ring of rings) items.push({ row: i, polygon: ring, bounds: boundsOf(ring) });
  }
  return { items, n: rows };
}
