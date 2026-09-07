/**
 * The strip index as plain rows.
 *
 * There are a few hundred strips rather than tens of thousands of frames, so
 * the strips table is turned into objects (a table and a small map are all
 * the view needs) instead of the typed-array treatment the catalog gets.
 * Datetime columns arrive as int64 epoch milliseconds with a `_ms` suffix per
 * the contract; the plain names are accepted too so that a backend that
 * passes timestamps straight through still works.
 */
import { tableFromIPC } from 'apache-arrow';
import { bandsInclude, splitBands } from './bands';

export interface StripRow {
  strip_id: string;
  orbit: number;
  seq_id: string;
  band: string;
  /** Amendment 2026-09-07: `JIRAM` or `JunoCam`. */
  instrument: string;
  /** The strip's bands; one for JIRAM, several for a JunoCam swath. */
  bands: string[];
  n_frames: number;
  time_start_ms: number;
  time_end_ms: number;
  time_mid_ms: number;
  center_lat: number;
  center_lon_east: number;
  km_per_px: number;
  resolution_class: string;
  valid_frac: number;
  dayside_frac: number;
  median_emission: number;
  lat_min: number;
  lat_max: number;
  rows: number;
  cols: number;
  [column: string]: unknown;
}

function num(value: unknown): number {
  if (value === null || value === undefined) return NaN;
  if (typeof value === 'bigint') return Number(value);
  const n = Number(value);
  return Number.isNaN(n) ? NaN : n;
}

function pick(row: Record<string, unknown>, ...names: string[]): unknown {
  for (const name of names) if (row[name] !== undefined && row[name] !== null) return row[name];
  return undefined;
}

/** Parse `/api/strips.arrow` into rows the strips view can sort and filter. */
export function parseStrips(bytes: ArrayBuffer | Uint8Array): StripRow[] {
  const table = tableFromIPC(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
  const out: StripRow[] = [];
  for (let i = 0; i < table.numRows; i++) {
    const raw = table.get(i)?.toJSON() as Record<string, unknown> | undefined;
    if (!raw) continue;
    out.push({
      ...raw,
      strip_id: String(raw.strip_id ?? ''),
      orbit: num(raw.orbit),
      seq_id: String(raw.seq_id ?? ''),
      band: String(raw.band ?? ''),
      // A row from before the amendment is a JIRAM row whose one band is its
      // half, which is exactly what the column used to mean.
      instrument: String(raw.instrument ?? 'JIRAM'),
      bands: splitBands(raw.bands ?? raw.band ?? ''),
      n_frames: num(raw.n_frames),
      time_start_ms: num(pick(raw, 'time_start_ms', 'time_start')),
      time_end_ms: num(pick(raw, 'time_end_ms', 'time_end')),
      time_mid_ms: num(pick(raw, 'time_mid_ms', 'time_mid')),
      center_lat: num(raw.center_lat),
      center_lon_east: num(raw.center_lon_east),
      km_per_px: num(raw.km_per_px),
      resolution_class: String(raw.resolution_class ?? ''),
      valid_frac: num(raw.valid_frac),
      dayside_frac: num(raw.dayside_frac),
      median_emission: num(raw.median_emission),
      lat_min: num(raw.lat_min),
      lat_max: num(raw.lat_max),
      rows: num(raw.rows),
      cols: num(raw.cols),
    });
  }
  return out;
}

export interface StripFilters {
  latBand: string;
  instrument: 'all' | 'JIRAM' | 'JunoCam';
  /** A band name the strip must carry, or `all`. */
  band: string;
  timeMin: number | null;
  timeMax: number | null;
  kmPerPxMax: number | null;
  validMin: number;
  daysideOnly: boolean;
  orbits: number[] | null;
}

export const DEFAULT_STRIP_FILTERS: StripFilters = {
  latBand: 'all',
  instrument: 'all',
  band: 'all',
  timeMin: null,
  timeMax: null,
  kmPerPxMax: null,
  validMin: 0,
  daysideOnly: false,
  orbits: null,
};

/**
 * Latitude and epoch are overlap tests -- a strip is kept when its own span
 * meets the query's, which is the question a coverage filter asks -- while
 * resolution, valid fraction and dayside are thresholds on the strip's own
 * scalar.  Same rule as `strips.load_strips`.  Instrument is equality and
 * band is membership, because a JunoCam strip holds several bands at once.
 */
export function filterStrips(rows: StripRow[], filters: StripFilters, latLimits: [number, number]): StripRow[] {
  return rows.filter((row) => {
    if (filters.instrument !== 'all' && row.instrument.toUpperCase() !== filters.instrument.toUpperCase()) {
      return false;
    }
    if (filters.band !== 'all') {
      // A JunoCam strip carries several bands and matches on any of them; a
      // JIRAM strip's single band is the same test with one token.
      const field = row.bands.length > 0 ? row.bands.join(';') : row.band;
      if (!bandsInclude(field, filters.band)) return false;
    }
    if (filters.latBand !== 'all') {
      const [lo, hi] = latLimits;
      const low = Number.isFinite(row.lat_min) ? row.lat_min : row.center_lat;
      const high = Number.isFinite(row.lat_max) ? row.lat_max : row.center_lat;
      if (!(high >= lo && low <= hi)) return false;
    }
    if (filters.timeMin !== null && Number.isFinite(row.time_end_ms) && row.time_end_ms < filters.timeMin) return false;
    if (filters.timeMax !== null && Number.isFinite(row.time_start_ms) && row.time_start_ms > filters.timeMax) return false;
    if (filters.kmPerPxMax !== null && Number.isFinite(row.km_per_px) && row.km_per_px > filters.kmPerPxMax) return false;
    if (filters.validMin > 0 && !(row.valid_frac >= filters.validMin)) return false;
    if (filters.daysideOnly && !(row.dayside_frac > 0)) return false;
    if (filters.orbits && filters.orbits.length > 0 && !filters.orbits.includes(row.orbit)) return false;
    return true;
  });
}

/** The band names the loaded strips actually carry, for an instrument choice. */
export function stripBandOptions(rows: StripRow[], instrument: 'all' | 'JIRAM' | 'JunoCam'): string[] {
  const names = new Set<string>();
  for (const row of rows) {
    if (instrument !== 'all' && row.instrument.toUpperCase() !== instrument.toUpperCase()) continue;
    for (const band of row.bands.length > 0 ? row.bands : [row.band]) if (band) names.add(band);
  }
  return [...names].sort();
}
