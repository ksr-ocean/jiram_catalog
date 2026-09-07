import { ApiError } from './client';
export interface Reference {
  id: string;
  title: string;
  url: string;
  kind: string;
  description: string;
  quantitative_ready: boolean;
  coordinate_note?: string;
}
export interface CoverageRow {
  instrument: string;
  orbit: number;
  archive_known: number;
  labels_indexed: number;
  pixels_local: number;
  geometry_available: number;
  quality_assessed: number;
  eligible: number;
  excluded: number;
  unassessed: number;
  stacks: number;
  strips: number;
  status: string;
}
export interface Coverage {
  policy_version: string;
  generated_utc: string;
  sources: { name: string; updated_utc: string }[];
  rows: CoverageRow[];
  policy: { summary: string; exclusions: unknown };
  references: Reference[];
}
export interface ArchiveItem {
  product_id: string;
  observation_id: string;
  orbit: number;
  start_time: string;
  bands: string[] | string;
  target: string;
  source_url: string;
  label_url: string;
  status: string;
  reason: string;
}
export interface Readiness {
  ready: boolean;
  reasons: string[];
  n_observations: number;
  n_versions_removed: number;
  band: string;
  norm: string;
  units: string;
  km_per_px: number;
  times: string[];
  gaps_s: number[];
  runs: {
    first: number;
    last: number;
    dt_s: number;
    common_valid_frac: number;
    n_frames: number;
  }[];
  n_realizations: number;
  provenance: unknown;
}
export interface Scene {
  kind: 'stack' | 'strip';
  id: string;
  t: number;
  band: string | null;
  norm: string;
}
export interface Comparison {
  compatible: boolean;
  reasons: string[];
  dt_s: number | null;
  common_valid_frac: number | null;
  registration: {
    dy_px: number | null;
    dx_px: number | null;
    correlation: number | null;
    status: string;
    sample_stride?: number;
    sample_km_per_px?: number;
  };
  predicted_displacement_px: number | null;
  navigation_error_px: number | null;
  velocity_uncertainty_m_s: number | null;
  left: Record<string, unknown>;
  right: Record<string, unknown>;
  provenance: unknown;
}
export interface Vectors {
  status: string;
  reason?: string;
  units: string;
  provenance: unknown;
  features: { x_km: number; y_km: number; u: number; v: number }[];
}
export interface PopulationGroup {
  units?: string;
  instrument: string;
  band: string;
  norm: string;
  resolution_class: string | number;
  n_observations: number;
  n_passes: number;
  k: number[];
  E: number[];
  E_stderr: (number | null)[] | null;
  S2: number[];
  r_m: number[];
  fit: Record<string, unknown>;
  diagnostics: unknown;
  provenance: unknown;
}
export interface Population {
  groups: PopulationGroup[];
  excluded: { id: string; reason: string }[];
  recipe: unknown;
}
export interface Matches {
  items: {
    product_id: string;
    instrument: string;
    dt_s: number;
    overlap_fraction: number;
    overlap_method: string;
    native_pixel_km: number;
    bands: string[] | string;
  }[];
  reference: unknown;
  limitations: string[] | string;
}
async function json<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    path,
    body === undefined
      ? undefined
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const problem = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(
      typeof problem.detail === 'string' ? problem.detail : JSON.stringify(problem.detail),
      response.status,
      path,
    );
  }
  return response.json() as Promise<T>;
}
export const researchApi = {
  coverage: () => json<Coverage>('/api/coverage'),
  references: () => json<{ items: Reference[] }>('/api/references'),
  archive: (instrument: string, orbit: string, q: string, offset = 0) =>
    json<{ total: number; items: ArchiveItem[]; offset: number; limit: number }>(
      `/api/archive?${new URLSearchParams({ instrument, ...(orbit ? { orbit } : {}), q, offset: String(offset), limit: '100' })}`,
    ),
  readiness: (id: string, band: string | null, norm: string) =>
    json<Readiness>(
      `/api/science/stacks/${id}/readiness?${new URLSearchParams({ ...(band ? { band } : {}), norm })}`,
    ),
  compare: (left: Scene, right: Scene, speed_m_s: number, navigation_error_px: number | null) =>
    json<Comparison>('/api/science/compare', { left, right, speed_m_s, navigation_error_px }),
  vectors: (id: string, t: number) => json<Vectors>(`/api/science/stacks/${id}/vectors?t=${t}`),
  population: (
    strip_ids: string[],
    band: string | null,
    norm: string,
    k_min?: number,
    k_max?: number,
  ) => json<Population>('/api/science/population', { strip_ids, band, norm, k_min, k_max }),
  matches: (product_id: string) =>
    json<Matches>('/api/science/matches', {
      product_id,
      max_dt_s: 3600,
      min_overlap: 0.1,
      limit: 20,
    }),
};
