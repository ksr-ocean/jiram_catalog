/**
 * The one place that talks to the backend.
 *
 * Every call goes through `request`, which turns a non-2xx answer into an
 * `ApiError` carrying the contract's `detail` string.  Views never call
 * `fetch` themselves, so "shows a loading state and an error toast" is a
 * property of the store rather than a discipline each view has to keep.
 */
import type {
  AppConfig,
  CatalogSummary,
  FrameDetail,
  ImagePayload,
  JobRecord,
  SelectionDetail,
  SelectionRecord,
  StackListing,
  StackMeta,
  StripMeta,
  StripStats,
} from './types';

/** Same origin in production; `E2E_BASE_URL`/dev proxy handle the rest. */
export const API_BASE = '';

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly path: string) {
    super(message);
    this.name = 'ApiError';
  }
}

function url(path: string, params?: Record<string, string | number | boolean | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
  }
  const suffix = query.toString();
  return `${API_BASE}${path}${suffix ? `?${suffix}` : ''}`;
}

async function request(path: string, init?: RequestInit, params?: Record<string, string | number | boolean | undefined>): Promise<Response> {
  const target = url(path, params);
  let response: Response;
  try {
    response = await fetch(target, init);
  } catch (error) {
    throw new ApiError(`network error: ${(error as Error).message}`, 0, target);
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      /* a non-JSON error body (the static 503 hint) keeps the status line */
    }
    throw new ApiError(detail, response.status, target);
  }
  return response;
}

async function getJson<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  return (await request(path, undefined, params)).json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return response.json() as Promise<T>;
}

export const api = {
  config: () => getJson<AppConfig>('/api/config'),
  health: () => getJson<{ ok: boolean }>('/api/health'),

  async framesArrow(): Promise<ArrayBuffer> {
    return (await request('/api/catalog/frames.arrow')).arrayBuffer();
  },
  summary: (params: Record<string, string>) => getJson<CatalogSummary>('/api/catalog/summary', params),
  frame: (productId: string) => getJson<FrameDetail>(`/api/catalog/frame/${encodeURIComponent(productId)}`),

  selections: () => getJson<SelectionRecord[]>('/api/selections'),
  selection: (id: string) => getJson<SelectionDetail>(`/api/selections/${encodeURIComponent(id)}`),
  createSelection: (body: { name: string; product_ids: string[]; halves?: string[]; note?: string }) =>
    postJson<SelectionRecord>('/api/selections', body),
  async deleteSelection(id: string): Promise<void> {
    await request(`/api/selections/${encodeURIComponent(id)}`, { method: 'DELETE' });
  },

  stacks: () => getJson<StackListing[]>('/api/stacks'),
  stackMeta: (id: string) => getJson<StackMeta>(`/api/stacks/${id}/meta`),
  /**
   * `band` is required by the contract once a stack has more than one.
   *
   * `norm` and `stretch` are the photometry amendment's: the illumination
   * model the server divides out before the stretch, and whether the eight
   * bits are laid out linearly or through `asinh`.  Both are omitted when
   * they are absent, so a request from a viewer that has not been told about
   * them still means "the product's own default".
   */
  stackFrameUrl: (
    id: string,
    t: number,
    vmin?: number,
    vmax?: number,
    maxPx = 1600,
    band?: string | null,
    norm?: string | null,
    stretch?: string | null,
  ) =>
    url(`/api/stacks/${id}/frame/${t}.png`, {
      vmin,
      vmax,
      max_px: maxPx,
      band: band ?? undefined,
      norm: norm ?? undefined,
      stretch: stretch ?? undefined,
    }),
  /**
   * The RGB composite of the amendment: one stretch pair per channel, so the
   * three bands can be balanced against each other rather than sharing a
   * range that suits none of them.
   */
  stackRgbUrl: (
    id: string,
    t: number,
    stretch: { r: [number, number]; g: [number, number]; b: [number, number] },
    maxPx = 1600,
    norm?: string | null,
    mode?: string | null,
  ) =>
    url(`/api/stacks/${id}/frame/${t}/rgb.png`, {
      vmin_r: stretch.r[0],
      vmax_r: stretch.r[1],
      vmin_g: stretch.g[0],
      vmax_g: stretch.g[1],
      vmin_b: stretch.b[0],
      vmax_b: stretch.b[1],
      max_px: maxPx,
      norm: norm ?? undefined,
      stretch: mode ?? undefined,
    }),
  stackEmissionUrl: (id: string, t: number, maxPx = 1600) =>
    url(`/api/stacks/${id}/frame/${t}/emission.png`, { max_px: maxPx }),
  movieUrl: (id: string, band?:string|null, norm?:string) => url(`/api/stacks/${id}/movie`,{band:band??undefined,norm}),
  renderMovie: (id: string, body: { fps?: number; pct?: [number, number]; cmap?: string; band?:string|null; norm?:string }) =>
    postJson<{ job_id: string }>(`/api/stacks/${id}/movie`, body),
  buildStack: (body: {
    region: string;
    band: string;
    level: string;
    orbits?: number[];
    selection_id?: string;
    max_emission?: number;
    instrument?: string;
    bands?: string[];
    quality_min?: string;
  }) => postJson<{ job_id: string }>('/api/stacks/build', body),
  exportTriples: (id: string, body: { out_dir?: string; dt_tol?: number; min_frames?: number; crop_to_valid?: boolean; band?:string|null; norm?:string }) =>
    postJson<{ job_id: string }>(`/api/stacks/${id}/export`, body),

  async stripsArrow(): Promise<ArrayBuffer> {
    return (await request('/api/strips.arrow')).arrayBuffer();
  },
  stripMeta: (id: string) => getJson<StripMeta>(`/api/strips/${encodeURIComponent(id)}/meta`),
  stripImageUrl: (
    id: string,
    vmin?: number,
    vmax?: number,
    maxPx = 1600,
    band?: string | null,
    norm?: string | null,
    stretch?: string | null,
  ) =>
    url(`/api/strips/${encodeURIComponent(id)}/image.png`, {
      vmin,
      vmax,
      max_px: maxPx,
      band: band ?? undefined,
      norm: norm ?? undefined,
      stretch: stretch ?? undefined,
    }),
  /** The statistics of one band under one illumination model; both travel. */
  stripStats: (id: string, band?: string | null, norm?: string | null) =>
    getJson<StripStats>(`/api/strips/${encodeURIComponent(id)}/stats`, {
      band: band ?? undefined,
      norm: norm ?? undefined,
    }),

  jobs: () => getJson<JobRecord[]>('/api/jobs'),
  job: (id: string) => getJson<JobRecord>(`/api/jobs/${encodeURIComponent(id)}`),
  async cancelJob(id: string): Promise<void> {
    await request(`/api/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' });
  },
};

/**
 * Fetch a grayscale PNG and keep both its pixels and a bitmap.
 *
 * The pixels are kept because the colour map is applied in the browser; the
 * bounds come from the contract's `X-Bounds` header, which is the served
 * image's extent in km after the server's integer striding.
 */
export async function fetchImage(target: string): Promise<ImagePayload> {
  const response = await request(target);
  const boundsHeader = response.headers.get('X-Bounds');
  const stride = Number(response.headers.get('X-Stride') ?? 1);
  const blob = await response.blob();
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new ApiError('no 2d canvas context', 0, target);
  context.drawImage(bitmap, 0, 0);
  const gray = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
  const bounds = boundsHeader
    ? (boundsHeader.split(',').map(Number) as [number, number, number, number])
    : ([0, bitmap.width, 0, bitmap.height] as [number, number, number, number]);
  return { bitmap, gray, width: bitmap.width, height: bitmap.height, bounds, stride };
}
