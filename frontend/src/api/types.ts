/** Response shapes of `docs/specs/2026-09-06_api_contract.md`. */

export interface AppConfig {
  mirror: string;
  paper_data: string;
  version: string;
  counts: {
    frames_on_planet: number;
    strips: number;
    stacks: number;
    selections: number;
  };
  has_trackability: boolean;
}

export interface CatalogSummary {
  n: number;
  by_lat_band: { band: string; n: number }[];
  by_orbit: { orbit: number; n: number }[];
  by_month: { month: string; n: number }[];
}

export interface SelectionRecord {
  id: string;
  name: string;
  created: string;
  n_frames: number;
  n_orbits: number;
  lat_min: number | null;
  lat_max: number | null;
  note?: string | null;
}

export interface SelectionDetail extends SelectionRecord {
  product_ids: string[];
  halves?: string[];
}

export interface StackListing {
  id: string;
  region: string;
  band: string;
  level: string;
  path: string;
  n_time: number;
  shape: [number, number];
  km_per_px: number;
  size_bytes: number;
  has_movie: boolean;
  movie_path?: string | null;
}

export interface GeoJsonLineString {
  type: 'Feature';
  geometry: { type: 'LineString'; coordinates: [number, number][] };
  properties?: Record<string, unknown>;
}

export interface GeoJsonCollection {
  type: 'FeatureCollection';
  features: GeoJsonLineString[];
}

export interface PerTimeRecord {
  i: number;
  product_id?: string | null;
  seq_id?: string | null;
  orbit?: number | null;
  n_frames?: number | null;
  bore_emission?: number | null;
}

export interface StackMeta {
  id: string;
  region: string;
  band: string;
  level: string;
  km_per_px: number;
  x_km: [number, number];
  y_km: [number, number];
  shape: [number, number];
  times: string[];
  per_time: PerTimeRecord[];
  stretch: { p1: number; p99: number };
  graticule: GeoJsonCollection;
  attrs?: Record<string, unknown>;
}

export interface StripMeta {
  attrs?: Record<string, unknown>;
  x_km: [number, number];
  y_km: [number, number];
  shape: [number, number];
  stretch: { p1: number; p99: number };
  graticule: GeoJsonCollection;
  local_time_contours?: GeoJsonCollection;
}

export interface StripStats {
  k: number[];
  E: number[];
  k_x: number[];
  P_x: number[];
  k_y: number[];
  P_y: number[];
  r_m: number[];
  S2: number[];
  S3: number[];
  attrs?: Record<string, unknown>;
}

export type JobStatus = 'queued' | 'running' | 'done' | 'failed';

export interface JobRecord {
  id: string;
  kind: string;
  status: JobStatus;
  progress: number;
  message?: string | null;
  started?: string | null;
  finished?: string | null;
  result?: Record<string, unknown> | null;
}

export interface FrameDetail {
  [column: string]: unknown;
  product_id: string;
  halves?: string[];
}

/** A PNG plus the geometry headers the contract puts beside it. */
export interface ImagePayload {
  bitmap: ImageBitmap;
  gray: Uint8ClampedArray;
  width: number;
  height: number;
  bounds: [number, number, number, number];
  stride: number;
}
