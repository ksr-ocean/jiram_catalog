/** A tiny `frames.arrow` stand-in with the contract's column names and types. */
import { tableFromArrays, tableToIPC, type Table } from 'apache-arrow';

export interface SyntheticSpec {
  productId: string[];
  half: string[];
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
  //            0        1        2        3        4        5
  productId: ['P0', 'P1', 'P2', 'P3', 'P4', 'P5'],
  half: ['L', 'M', 'L', 'M', 'L', 'M'],
  orbit: [4, 4, 11, 11, 38, 38],
  startTimeMs: [
    Date.UTC(2016, 11, 11), Date.UTC(2016, 11, 11),
    Date.UTC(2018, 3, 1), Date.UTC(2018, 3, 1),
    Date.UTC(2021, 8, 2), Date.UTC(2021, 8, 2),
  ],
  boreLat: [75, -75, 5, NaN, 65, 40],
  boreLonEast: [10, 200, 350, 100, -30, 45],
  boreEmission: [20, 70, NaN, 30, 10, 85],
  onPlanetFrac: [1.0, 0.4, 0.9, 0.2, NaN, 0.75],
  medianPixelKm: [12, 40, NaN, 8, 15, 60],
  daysideFrac: [0.9, 0.0, 0.5, 0.0, 0.3, 0.0],
  hasPartner: [1, 0, 1, 0, 1, 0],
};

export function syntheticTable(spec: SyntheticSpec = SPEC): Table {
  return tableFromArrays({
    product_id: spec.productId,
    half: spec.half,
    band: spec.half.map(() => 'M'),
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
}

export function syntheticIPC(spec: SyntheticSpec = SPEC): Uint8Array {
  return tableToIPC(syntheticTable(spec), 'stream');
}
