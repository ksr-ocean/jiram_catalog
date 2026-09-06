/**
 * The seven latitude bands of the trackability report, shared by the
 * catalog's summary chart and the strips filter so the GUI's vocabulary
 * matches the command line's.
 */

export const LAT_BAND_EDGES: readonly number[] = [-90, -60, -30, -10, 10, 30, 60, 90];

export const LAT_BAND_NAMES: readonly string[] = [
  'S polar',
  'S mid',
  'S low',
  'equator',
  'N low',
  'N mid',
  'N polar',
];

/** The band a latitude falls in, or `null` when the latitude is missing. */
export function latBand(lat: number): string | null {
  if (!Number.isFinite(lat)) return null;
  // np.digitize(value, edges[1:-1], right=False) clipped to the band count.
  let index = 0;
  for (let i = 1; i < LAT_BAND_EDGES.length - 1; i++) {
    if (lat >= LAT_BAND_EDGES[i]) index = i;
  }
  return LAT_BAND_NAMES[Math.min(index, LAT_BAND_NAMES.length - 1)];
}

/** The `[lo, hi]` degrees a band name spans; the whole sphere for `all`. */
export function latBandLimits(name: string): [number, number] {
  const index = LAT_BAND_NAMES.indexOf(name);
  if (index < 0) return [-90, 90];
  return [LAT_BAND_EDGES[index], LAT_BAND_EDGES[index + 1]];
}

/** Counts per band over a latitude column, in band order. */
export function binByLatBand(lat: ArrayLike<number>, indices?: ArrayLike<number>): Map<string, number> {
  const counts = new Map<string, number>();
  for (const name of LAT_BAND_NAMES) counts.set(name, 0);
  const n = indices ? indices.length : lat.length;
  for (let k = 0; k < n; k++) {
    const band = latBand(lat[indices ? indices[k] : k]);
    if (band !== null) counts.set(band, (counts.get(band) ?? 0) + 1);
  }
  return counts;
}
