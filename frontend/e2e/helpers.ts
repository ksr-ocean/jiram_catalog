import { expect, type Locator, type Page } from '@playwright/test';

export interface DebugState {
  n_points: number;
  n_filtered: number;
  selection_n: number;
  view: string;
  stack_id: string | null;
  level: string | null;
  t: number;
  cmap: string;
  stats_visible: boolean;
  /** Amendment 2026-09-07. */
  instrument_filter: string;
  band: string | null;
  composite: boolean;
  n_footprints: number;
  strip_band: string | null;
  strip_composite: boolean;
  /** Photometry amendment 2026-09-07: the wire spelling of each viewer's norm. */
  norm: string;
  stretch_mode: string;
  strip_norm: string;
  strip_stretch_mode: string;
}

/**
 * How many JunoCam images the backend has indexed.
 *
 * Zero on a mirror that has not run `junocam geo` yet, and on any backend
 * from before the amendment; every JunoCam test skips on it rather than
 * failing, so this suite stays green either way.
 */
export async function junocamCount(page: Page): Promise<number> {
  try {
    const response = await page.request.get('/api/config');
    if (!response.ok()) return 0;
    const config = (await response.json()) as { counts?: { junocam_images?: number } };
    return config.counts?.junocam_images ?? 0;
  } catch {
    return 0;
  }
}

/** The app's own state, read from the hidden `#debug-state` element. */
export async function debugState(page: Page): Promise<DebugState> {
  const text = await page.locator('#debug-state').textContent();
  return JSON.parse(text ?? '{}') as DebugState;
}

/** Open the app and wait until the catalog Arrow table has been parsed. */
export async function openApp(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page.locator('#debug-state')).toBeAttached();
  await expect
    .poll(async () => (await debugState(page)).n_points, { timeout: 120_000, intervals: [500] })
    .toBeGreaterThan(0);
}

/** Wait until the debug state satisfies a predicate, then return it. */
export async function waitForState(
  page: Page,
  predicate: (state: DebugState) => boolean,
  timeout = 90_000,
): Promise<DebugState> {
  await expect.poll(async () => predicate(await debugState(page)), { timeout, intervals: [400] }).toBe(true);
  return debugState(page);
}

/** The centre of an element, in page coordinates. */
export async function centreOf(locator: Locator): Promise<{ x: number; y: number; width: number; height: number }> {
  const box = await locator.boundingBox();
  if (!box) throw new Error('element has no box');
  return { x: box.x + box.width / 2, y: box.y + box.height / 2, width: box.width, height: box.height };
}

/** The first non-transparent pixel of the colour-mapped frame canvas. */
export async function sampleFrameCanvas(page: Page): Promise<[number, number, number, number] | null> {
  return page.evaluate(() => {
    const canvas = document.querySelector<HTMLCanvasElement>('[data-testid="frame-canvas"][data-loaded="true"]');
    if (!canvas) return null;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) return null;
    const { data, width, height } = context.getImageData(0, 0, canvas.width, canvas.height);
    // Walk from the middle outwards: the corners of a polar mosaic are empty.
    const centre = (Math.floor(height / 2) * width + Math.floor(width / 2)) * 4;
    for (let offset = 0; offset < data.length; offset += 4) {
      const p = (centre + offset) % data.length;
      if (data[p + 3] > 0) return [data[p], data[p + 1], data[p + 2], data[p + 3]] as [number, number, number, number];
    }
    return null;
  });
}

/**
 * A summary of the colour-mapped frame canvas.
 *
 * A single pixel is too weak a signature: a dark pixel of a grey-mapped band
 * and the same pixel of a composite can agree by accident, and the first
 * version of the composite test failed on exactly that.  `signature` is a
 * hash over every seventh visible pixel, which two different pictures do not
 * collide on, and `nonGray` counts pixels whose channels differ, which the
 * grey lookup table cannot produce at all.
 */
export async function frameCanvasStats(
  page: Page,
): Promise<{ visible: number; nonGray: number; signature: number } | null> {
  return page.evaluate(() => {
    const canvas = document.querySelector<HTMLCanvasElement>('[data-testid="frame-canvas"][data-loaded="true"]');
    if (!canvas) return null;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) return null;
    const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
    let visible = 0;
    let nonGray = 0;
    let signature = 0;
    for (let p = 0; p < data.length; p += 28) {
      if (data[p + 3] === 0) continue;
      visible += 1;
      const r = data[p];
      const g = data[p + 1];
      const b = data[p + 2];
      if (Math.abs(r - g) > 8 || Math.abs(g - b) > 8) nonGray += 1;
      signature = (signature * 31 + r * 7 + g * 13 + b * 17) % 1000000007;
    }
    return { visible, nonGray, signature };
  });
}

/**
 * The two counts in the catalog table's caption: the client's and the
 * server's, for the same filters.
 *
 * The server's arrives on its own round trip, so a test that reads the
 * caption the instant a filter changes reads the previous answer; poll this
 * until the two agree rather than asserting once and hoping.
 */
export async function captionCounts(page: Page): Promise<[number, number] | null> {
  const caption = (await page.locator('[data-testid="table-caption"]').textContent()) ?? '';
  const numbers = (caption.replace(/,/g, '').match(/\d+/g) ?? []).map(Number);
  return numbers.length >= 2 ? [numbers[0], numbers[1]] : null;
}
