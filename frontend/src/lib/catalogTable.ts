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

/**
 * A `list<float32>` column as one values array plus row offsets.
 *
 * `offsets[i]` .. `offsets[i + 1]` is row `i`'s slice of `values`, which is
 * the same shape Arrow itself stores and lets a footprint reach the geometry
 * code as a `subarray` rather than a fresh array per row.
 */
export interface ListColumn {
  readonly values: Float32Array;
  readonly offsets: Int32Array;
}

export interface CatalogColumns {
  readonly n: number;
  readonly productId: string[];
  readonly half: string[];
  readonly band: string[];
  readonly seqId: string[];
  /** Amendment 2026-09-07: `JIRAM` or `JunoCam`; `JIRAM` on an older backend. */
  readonly instrument: string[];
  /** The row's bands: the half for JIRAM, `;`-joined filters for JunoCam. */
  readonly bands: string[];
  readonly qualityTier: string[];
  readonly observationId?: string[];
  readonly trackabilityStatus?: string[];
  readonly qualityStatus?: string[];
  readonly qualityReason?: string[];
  readonly fpLon: ListColumn;
  readonly fpLat: ListColumn;
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

const EMPTY_LIST: ListColumn = { values: new Float32Array(0), offsets: new Int32Array(1) };

/** An empty list column of `n` rows: every offset zero, no values. */
function emptyList(n: number): ListColumn {
  return { values: new Float32Array(0), offsets: new Int32Array(n + 1) };
}

/**
 * A `list<float32>` column flattened into values and offsets.
 *
 * Arrow's own list vector already stores exactly this, but its offsets are
 * per chunk, and a missing column has to degrade to "no vertices" rather than
 * throw -- a backend that predates the amendment sends neither footprint
 * column and the map must still draw its points.
 */
export function listColumn(table: Table, name: string): ListColumn {
  const vector = table.getChild(name) as Vector | null;
  if (!vector) return emptyList(table.numRows);
  const offsets = new Int32Array(table.numRows + 1);
  const chunks: ArrayLike<number>[] = [];
  let total = 0;
  for (let i = 0; i < table.numRows; i++) {
    const value = vector.get(i) as { toArray?: () => ArrayLike<number>; length?: number } | null;
    let flat: ArrayLike<number> | null = null;
    if (value !== null && value !== undefined) {
      if (typeof value.toArray === 'function') flat = value.toArray();
      else if (typeof value.length === 'number') flat = value as unknown as ArrayLike<number>;
    }
    const length = flat ? flat.length : 0;
    chunks.push(flat ?? []);
    total += length;
    offsets[i + 1] = total;
  }
  const values = new Float32Array(total);
  let at = 0;
  for (const chunk of chunks) {
    for (let j = 0; j < chunk.length; j++) values[at++] = Number(chunk[j]);
  }
  return { values, offsets };
}

/** A utf8 column as a plain string array; `fallback` when the column is absent. */
export function stringColumn(table: Table, name: string, fallback = ''): string[] {
  const vector = table.getChild(name) as Vector | null;
  const out = new Array<string>(table.numRows);
  if (!vector) return out.fill(fallback);
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
    // A backend from before the amendment sends none of these five: every row
    // is then a JIRAM row of tier A with no outline, which is what it was.
    instrument: stringColumn(table, 'instrument', 'JIRAM'),
    bands: bandsColumn(table),
    qualityTier: stringColumn(table, 'quality_tier', 'A'),
    observationId: stringColumn(table, 'observation_id'),
    trackabilityStatus: stringColumn(table, 'trackability_status', 'unassessed'),
    qualityStatus: stringColumn(table, 'quality_status', 'unassessed'),
    qualityReason: stringColumn(table, 'quality_reason'),
    fpLon: listColumn(table, 'fp_lon'),
    fpLat: listColumn(table, 'fp_lat'),
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

/**
 * The distinct band names each instrument actually has, for the band filter.
 *
 * The options have to come from the data rather than from a constant: which
 * JunoCam filters are in the mirror depends on which orbits were indexed, and
 * offering a band nothing carries is offering an empty map.
 */
export function bandsByInstrument(columns: CatalogColumns): Record<string, string[]> {
  const found = new Map<string, Set<string>>();
  for (let i = 0; i < columns.n; i++) {
    const instrument = columns.instrument[i] || 'JIRAM';
    let set = found.get(instrument);
    if (!set) {
      set = new Set<string>();
      found.set(instrument, set);
    }
    const field = columns.bands[i];
    if (!field) continue;
    for (const name of field.split(';')) {
      const trimmed = name.trim();
      if (trimmed) set.add(trimmed);
    }
  }
  const out: Record<string, string[]> = {};
  for (const [instrument, set] of found) out[instrument] = [...set].sort();
  return out;
}

/**
 * The `bands` column, whatever shape it arrives in.
 *
 * The contract specifies a utf8 column with the names joined by `;`, but a
 * backend may equally send it as `list<utf8>`, and reading a list vector
 * through `String()` yields `[RED,GREEN,BLUE]`, which then looks like one
 * band with an odd name and quietly empties the band menu.  Both shapes are
 * normalised to the `;`-joined form the filters expect; a backend with no
 * `bands` column at all is a JIRAM-only one, whose band is its half.
 */
export function bandsColumn(table: Table): string[] {
  const vector = table.getChild('bands') as Vector | null;
  if (!vector) return stringColumn(table, 'half');
  const out = new Array<string>(table.numRows);
  for (let i = 0; i < table.numRows; i++) {
    const value = vector.get(i) as unknown;
    if (value === null || value === undefined) {
      out[i] = '';
    } else if (typeof value === 'string') {
      out[i] = value;
    } else if (Array.isArray(value)) {
      out[i] = value.map((name) => String(name)).join(';');
    } else if (typeof (value as { toArray?: unknown }).toArray === 'function') {
      out[i] = Array.from((value as { toArray: () => ArrayLike<unknown> }).toArray(), (name) =>
        String(name),
      ).join(';');
    } else {
      out[i] = String(value);
    }
  }
  return out;
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
  instrument: [],
  bands: [],
  qualityTier: [],
  fpLon: EMPTY_LIST,
  fpLat: EMPTY_LIST,
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
