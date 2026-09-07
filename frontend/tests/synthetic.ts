/**
 * A tiny `frames.arrow` stand-in with the contract's column names and types.
 *
 * Six JIRAM band-half rows, unchanged since the first version, and the two
 * JunoCam rows the amendment of 2026-09-07 makes possible: several bands in
 * one row, a quality tier, and a footprint outline -- one of them straddling
 * the 0/360 seam, because that is the case the drawing code has to get right.
 */
import {
  Field,
  Float32,
  List,
  Utf8,
  makeTable,
  tableFromArrays,
  tableToIPC,
  vectorFromArray,
  type Table,
} from 'apache-arrow';

export interface SyntheticSpec {
  productId: string[];
  half: string[];
  instrument: string[];
  bands: string[];
  qualityTier: string[];
  fpLon: number[][];
  fpLat: number[][];
  orbit: number[];
  startTimeMs: number[];
  boreLat: number[];
  boreLonEast: number[];
  boreEmission: number[];
  onPlanetFrac: number[];
  medianPixelKm: number[];
  daysideFrac: number[];
  hasPartner: number[];
}

export const SPEC: SyntheticSpec = {
  //            0     1     2     3     4     5      6         7
  productId: ['P0', 'P1', 'P2', 'P3', 'P4', 'P5', 'JC0', 'JC1'],
  half: ['L', 'M', 'L', 'M', 'L', 'M', '', ''],
  instrument: ['JIRAM', 'JIRAM', 'JIRAM', 'JIRAM', 'JIRAM', 'JIRAM', 'JunoCam', 'JunoCam'],
  bands: ['L', 'M', 'L', 'M', 'L', 'M', 'RED;GREEN;BLUE', 'RED;GREEN;BLUE;METHANE'],
  qualityTier: ['A', 'A', 'A', 'A', 'A', 'A', 'A', 'B'],
  // JC0's outline sits inside the map; JC1's straddles the seam.
  fpLon: [[], [], [], [], [], [], [10, 30, 30, 10], [350, 20, 20, 350]],
  fpLat: [[], [], [], [], [], [], [70, 70, 84, 84], [76, 76, 88, 88]],
  orbit: [4, 4, 11, 11, 38, 38, 38, 38],
  startTimeMs: [
    Date.UTC(2016, 11, 11), Date.UTC(2016, 11, 11),
    Date.UTC(2018, 3, 1), Date.UTC(2018, 3, 1),
    Date.UTC(2021, 8, 2), Date.UTC(2021, 8, 2),
    Date.UTC(2021, 8, 2), Date.UTC(2021, 8, 2),
  ],
  boreLat: [75, -75, 5, NaN, 65, 40, 80, 82],
  boreLonEast: [10, 200, 350, 100, -30, 45, 20, 5],
  boreEmission: [20, 70, NaN, 30, 10, 85, 50, 60],
  onPlanetFrac: [1.0, 0.4, 0.9, 0.2, NaN, 0.75, 0.3, 0.4],
  medianPixelKm: [12, 40, NaN, 8, 15, 60, 25, 30],
  daysideFrac: [0.9, 0.0, 0.5, 0.0, 0.3, 0.0, 0.0, 0.0],
  hasPartner: [1, 0, 1, 0, 1, 0, 0, 0],
};

/**
 * The two footprint columns.
 *
 * `tableFromArrays` builds flat columns only, so the `list<float32>` pair the
 * amendment adds is built as vectors and assigned in -- empty lists for the
 * JIRAM rows, which is what the contract says they carry.
 */
function footprintTable(spec: SyntheticSpec): Table {
  const listType = () => new List(new Field('item', new Float32(), true));
  const asVector = (rows: number[][]) =>
    vectorFromArray(rows.map((row) => Float32Array.from(row)), listType());
  // `makeTable`'s types describe flat arrays; a list vector is legal input
  // and the only way to build a `list<float32>` column here.
  const input = { fp_lon: asVector(spec.fpLon), fp_lat: asVector(spec.fpLat) } as never;
  return makeTable(input) as unknown as Table;
}

export function syntheticTable(spec: SyntheticSpec = SPEC): Table {
  const table = tableFromArrays({
    product_id: spec.productId,
    half: spec.half,
    band: spec.half.map((half) => half || 'RGB'),
    instrument: spec.instrument,
    bands: spec.bands,
    quality_tier: spec.qualityTier,
    seq_id: spec.productId.map((id) => `SEQ${id}`),
    orbit: Int16Array.from(spec.orbit),
    start_time_ms: BigInt64Array.from(spec.startTimeMs.map((t) => BigInt(t))),
    bore_lat: Float32Array.from(spec.boreLat),
    bore_lon_east: Float32Array.from(spec.boreLonEast),
    bore_emission: Float32Array.from(spec.boreEmission),
    on_planet_frac: Float32Array.from(spec.onPlanetFrac),
    median_pixel_km: Float32Array.from(spec.medianPixelKm),
    dayside_frac: Float32Array.from(spec.daysideFrac),
    min_lat: Float32Array.from(spec.boreLat),
    max_lat: Float32Array.from(spec.boreLat),
    lon_span_deg: Float32Array.from(spec.boreLat.map(() => 12)),
    pole_inside: Uint8Array.from(spec.boreLat.map((lat) => (Math.abs(lat) > 80 ? 1 : 0))),
    has_partner: Uint8Array.from(spec.hasPartner),
    best_dt_s: Float32Array.from(spec.hasPartner.map((p) => (p ? 30 : NaN))),
  });
  return table.assign(footprintTable(spec)) as unknown as Table;
}

export function syntheticIPC(spec: SyntheticSpec = SPEC): Uint8Array {
  return tableToIPC(syntheticTable(spec), 'stream');
}

/**
 * The same table with `bands` as `list<utf8>` instead of a `;`-joined string.
 *
 * The contract asks for the string, but a backend may send the list, and a
 * list read as a string reads back as `[RED,GREEN,BLUE]`, which would look
 * like one band with an odd name; the reader normalises both.
 */
export function syntheticTableListBands(spec: SyntheticSpec = SPEC): Table {
  const table = syntheticTable(spec);
  const listed = makeTable({
    bands: vectorFromArray(
      spec.bands.map((field) => field.split(';')),
      new List(new Field('item', new Utf8(), true)),
    ),
  } as never) as unknown as Table;
  return table.select(table.schema.fields.map((f) => f.name).filter((n) => n !== 'bands')).assign(listed) as Table;
}
