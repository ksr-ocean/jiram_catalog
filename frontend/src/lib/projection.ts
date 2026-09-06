/**
 * Map projections for the catalog point cloud.
 *
 * Both projections are plain functions of (latitude, east longitude) so the
 * catalog view can rewrite one pair of `Float32Array`s when the view mode
 * changes instead of asking the server for new coordinates.  They mirror
 * GUI v1's `views_catalog.display_frame` exactly, so a screenshot of v1 and
 * a screenshot of v2 put a frame in the same place.
 */

export type ViewMode = 'cyl' | 'N' | 'S';

export const VIEW_MODES: ViewMode[] = ['cyl', 'N', 'S'];

/** East longitude folded into `[0, 360)`; `NaN` stays `NaN`. */
export function wrapLon(lonEast: number): number {
  if (!Number.isFinite(lonEast)) return NaN;
  const wrapped = lonEast % 360;
  return wrapped < 0 ? wrapped + 360 : wrapped;
}

/** Cylindrical (plate carree) display coordinates: `[lon 0..360, lat]`. */
export function projectCylindrical(lat: number, lonEast: number): [number, number] {
  return [wrapLon(lonEast), Number.isFinite(lat) ? lat : NaN];
}

/**
 * Azimuthal-equidistant polar coordinates, degrees from the pole.
 *
 * `rho = 90 - |lat|` with east longitude as the polar angle, so the pole is
 * the origin and the equator the rim.  Points in the other hemisphere come
 * back as `NaN`, which draws nothing -- the same convention as v1.
 */
export function projectPolar(
  lat: number,
  lonEast: number,
  hemisphere: 'N' | 'S',
): [number, number] {
  if (!Number.isFinite(lat) || !Number.isFinite(lonEast)) return [NaN, NaN];
  const inside = hemisphere === 'N' ? lat >= 0 : lat <= 0;
  if (!inside) return [NaN, NaN];
  const rho = 90 - Math.abs(lat);
  const theta = (lonEast * Math.PI) / 180;
  return [rho * Math.cos(theta), rho * Math.sin(theta)];
}

/** One point in whichever view mode is current. */
export function project(lat: number, lonEast: number, mode: ViewMode): [number, number] {
  return mode === 'cyl' ? projectCylindrical(lat, lonEast) : projectPolar(lat, lonEast, mode);
}

/**
 * Project whole columns into a flat `[x0, y0, x1, y1, ...]` array, which is
 * what deck.gl wants for a binary position attribute.
 */
export function projectColumns(
  lat: ArrayLike<number>,
  lonEast: ArrayLike<number>,
  mode: ViewMode,
  out?: Float32Array,
): Float32Array {
  const n = lat.length;
  const xy = out && out.length === n * 2 ? out : new Float32Array(n * 2);
  for (let i = 0; i < n; i++) {
    const [x, y] = project(lat[i], lonEast[i], mode);
    xy[2 * i] = x;
    xy[2 * i + 1] = y;
  }
  return xy;
}

/** The data extent a view starts at: `[xMin, xMax, yMin, yMax]`. */
export function viewLimits(mode: ViewMode): [number, number, number, number] {
  return mode === 'cyl' ? [0, 360, -90, 90] : [-90, 90, -90, 90];
}

/** Graticule polylines for a view, in display coordinates. */
export function graticule(mode: ViewMode): { path: [number, number][] }[] {
  const paths: { path: [number, number][] }[] = [];
  if (mode === 'cyl') {
    for (let lat = -90; lat <= 90; lat += 10) {
      paths.push({ path: [[0, lat], [360, lat]] });
    }
    for (let lon = 0; lon <= 360; lon += 10) {
      paths.push({ path: [[lon, -90], [lon, 90]] });
    }
    return paths;
  }
  const hemisphere = mode;
  for (let absLat = 0; absLat < 90; absLat += 10) {
    const lat = hemisphere === 'N' ? absLat : -absLat;
    const circle: [number, number][] = [];
    for (let a = 0; a <= 360; a += 5) circle.push(projectPolar(lat, a, hemisphere));
    paths.push({ path: circle });
  }
  for (let lon = 0; lon < 360; lon += 30) {
    const lat0 = hemisphere === 'N' ? 0 : -0;
    paths.push({
      path: [projectPolar(hemisphere === 'N' ? 89.999 : -89.999, lon, hemisphere), projectPolar(lat0, lon, hemisphere)],
    });
  }
  return paths;
}
