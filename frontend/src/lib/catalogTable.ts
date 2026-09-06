/**
 * The catalog as typed arrays.
 *
 * `/api/catalog/frames.arrow` is an Arrow IPC stream of ~47,000 band-half
 * rows.  Arrow's numeric columns are already contiguous typed arrays, so the
 * whole catalog reaches deck.gl and the filter loop without ever becoming an
 * array of JavaScript objects -- which is what lets a filter change repaint
 * in a frame instead of a second.
 */
import { Table, tableFromIPC, type Vector } from 'apache-arrow';

export interface CatalogColumns {
  readonly n: number;
  readonly productId: string[];
  readonly half: string[];
  readonly band: string[];
  readonly seqId: string[];
  readonly orbit: Float32Array;
  readonly startTimeMs: Float64Array;
  readonly year: Int16Array;
  readonly boreLat: Float32Array;
  readonly boreLonEast: Float32Array;
  readonly boreEmission: Float32Array;
  readonly onPlanetFrac: Float32Array;
  readonly medianPixelKm: Float32Array;
  readonly daysideFrac: Float32Array;
  readonly minLat: Float32Array;
  readonly maxLat: Float32Array;
  readonly lonSpanDeg: Float32Array;
  readonly poleInside: Uint8Array;
  readonly hasPartner: Uint8Array;
  readonly bestDtS: Float32Array;
}

function nanColumn(n: number): Float32Array {
  return new Float32Array(n).fill(NaN);
}

/** A float column, zero-copy when Arrow already stores it as `Float32Array`. */
export function floatColumn(table: Table, name: string): Float32Array {
  const vector = table.getChild(name) as Vector | null;
  if (!vector) return nanColumn(table.numRows);
  if (vector.nullCount === 0) {
    const raw = vector.toArray();
    if (raw instanceof Float32Array) return raw;
    if (raw instanceof Float64Array) return Float32Array.from(raw);
  }
  const out = new Float32Array(table.numRows);
  for (let i = 0; i < table.numRows; i++) {
    const value = vector.get(i);
    out[i] = value === null || value === undefined ? NaN : Number(value);
  }
  return out;
}

/** An integer column widened to `Float32Array` so `NaN` can mean "missing". */
export function intColumn(table: Table, name: string): Float32Array {
  const vector = table.getChild(name) as Vector | null;
  if (!vector) return nanColumn(table.numRows);
  const out = new Float32Array(table.numRows);
  for (let i = 0; i < table.numRows; i++) {
    const value = vector.get(i);
    out[i] = value === null || value === undefined ? NaN : Number(value);
  }
  return out;
}

/** An int64 epoch-millisecond column as `Float64Array` (exact below 2^53). */
export function epochColumn(table: Table, name: string): Float64Array {
  const vector = table.getChild(name) as Vector | null;
  const out = new Float64Array(table.numRows);
  if (!vector) return out.fill(NaN);
  for (let i = 0; i < table.numRows; i++) {
    const value = vector.get(i);
    out[i] = value === null || value === undefined ? NaN : Number(value);
  }
  return out;
}

/** A boolean column as `0`/`1` bytes, which deck.gl accessors can index. */
export function boolColumn(table: Table, name: string): Uint8Array {
  const vector = table.getChild(name) as Vector | null;
  const out = new Uint8Array(table.numRows);
  if (!vector) return out;
  for (let i = 0; i < table.numRows; i++) out[i] = vector.get(i) ? 1 : 0;
  return out;
}

/** A utf8 column as a plain string array. */
export function stringColumn(table: Table, name: string): string[] {
  const vector = table.getChild(name) as Vector | null;
  const out = new Array<string>(table.numRows);
  if (!vector) return out.fill('');
  for (let i = 0; i < table.numRows; i++) {
    const value = vector.get(i);
    out[i] = value === null || value === undefined ? '' : String(value);
  }
  return out;
}

/** Split an Arrow table from `frames.arrow` into the columns the app draws. */
export function columnsFromTable(table: Table): CatalogColumns {
  const startTimeMs = epochColumn(table, 'start_time_ms');
  const year = new Int16Array(table.numRows);
  for (let i = 0; i < table.numRows; i++) {
    year[i] = Number.isFinite(startTimeMs[i]) ? new Date(startTimeMs[i]).getUTCFullYear() : 0;
  }
  return {
    n: table.numRows,
    productId: stringColumn(table, 'product_id'),
    half: stringColumn(table, 'half'),
    band: stringColumn(table, 'band'),
    seqId: stringColumn(table, 'seq_id'),
    orbit: intColumn(table, 'orbit'),
    startTimeMs,
    year,
    boreLat: floatColumn(table, 'bore_lat'),
    boreLonEast: floatColumn(table, 'bore_lon_east'),
    boreEmission: floatColumn(table, 'bore_emission'),
    onPlanetFrac: floatColumn(table, 'on_planet_frac'),
    medianPixelKm: floatColumn(table, 'median_pixel_km'),
    daysideFrac: floatColumn(table, 'dayside_frac'),
    minLat: floatColumn(table, 'min_lat'),
    maxLat: floatColumn(table, 'max_lat'),
    lonSpanDeg: floatColumn(table, 'lon_span_deg'),
    poleInside: boolColumn(table, 'pole_inside'),
    hasPartner: boolColumn(table, 'has_partner'),
    bestDtS: floatColumn(table, 'best_dt_s'),
  };
}

/** Parse the Arrow IPC bytes and split them in one step. */
export function columnsFromIPC(bytes: ArrayBuffer | Uint8Array): CatalogColumns {
  return columnsFromTable(tableFromIPC(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes)));
}

export const EMPTY_COLUMNS: CatalogColumns = {
  n: 0,
  productId: [],
  half: [],
  band: [],
  seqId: [],
  orbit: new Float32Array(0),
  startTimeMs: new Float64Array(0),
  year: new Int16Array(0),
  boreLat: new Float32Array(0),
  boreLonEast: new Float32Array(0),
  boreEmission: new Float32Array(0),
  onPlanetFrac: new Float32Array(0),
  medianPixelKm: new Float32Array(0),
  daysideFrac: new Float32Array(0),
  minLat: new Float32Array(0),
  maxLat: new Float32Array(0),
  lonSpanDeg: new Float32Array(0),
  poleInside: new Uint8Array(0),
  hasPartner: new Uint8Array(0),
  bestDtS: new Float32Array(0),
};
