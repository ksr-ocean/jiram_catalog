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
