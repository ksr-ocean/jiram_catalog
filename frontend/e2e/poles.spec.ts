import { expect, test } from '@playwright/test';
import { debugState, openApp, sampleFrameCanvas, waitForState } from './helpers';

/** Every stack id the chooser offers. */
async function stackIds(page: import('@playwright/test').Page): Promise<string[]> {
  return page
    .locator('[data-testid="stack-select"] option')
    .evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value).filter(Boolean));
}

/** The per-sequence stack the gate names, or any sequence stack, or the first. */
async function chooseStack(page: import('@playwright/test').Page, prefer = 'sequence'): Promise<string> {
  await page.locator('[data-testid="tab-poles"]').click();
  const select = page.locator('[data-testid="stack-select"]');
  await expect
    .poll(async () => select.locator('option').count(), { timeout: 90_000 })
    .toBeGreaterThan(1);
  const values = await select.locator('option').evaluateAll((options) =>
    options.map((option) => (option as HTMLOptionElement).value).filter(Boolean),
  );
  const chosen =
    values.find((value) => value === 'north_pole_paper/M_orbits4_sequence') ??
    values.find((value) => value.includes(prefer)) ??
    values[0];
  await select.selectOption(chosen);
  return chosen;
}

test.describe('poles view', () => {
  test('opens a sequence stack and loads its first frame', async ({ page }) => {
    await openApp(page);
    const chosen = await chooseStack(page);
    const state = await waitForState(page, (s) => s.stack_id === chosen && s.t === 0);
    expect(state.stack_id).toBe(chosen);
    await expect(page.locator('[data-testid="frame-canvas"][data-loaded="true"]').first()).toBeAttached({
      timeout: 90_000,
    });
    await expect(page.locator('[data-testid="frame-meta"]')).toContainText('km');
    await expect(page.locator('[data-testid="poles-image"] canvas').first()).toBeVisible();
  });

  test('the colour map is applied in the browser and changes the pixels', async ({ page }) => {
    await openApp(page);
    await chooseStack(page);
    await expect(page.locator('[data-testid="frame-canvas"][data-loaded="true"]').first()).toBeAttached({
      timeout: 90_000,
    });
    const before = await sampleFrameCanvas(page);
    expect(before, 'the frame canvas has visible pixels').not.toBeNull();

    await page.locator('[data-testid="cmap-select"]').selectOption('viridis');
    await waitForState(page, (s) => s.cmap === 'viridis');
    await expect
      .poll(async () => page.locator('[data-testid="frame-canvas"]').first().getAttribute('data-cmap'))
      .toBe('viridis');
    const after = await sampleFrameCanvas(page);
    expect(after).not.toBeNull();
    expect(after, 'the sampled pixel colour changed with the colour map').not.toEqual(before);
    // No refetch was needed: the colour map is a LUT over pixels already held.
    expect((await debugState(page)).cmap).toBe('viridis');
  });

  test('the time slider moves and the keyboard steps it', async ({ page }) => {
    await openApp(page);
    await chooseStack(page);
    await waitForState(page, (s) => s.stack_id !== null);
    const slider = page.locator('[data-testid="time-slider"]');
    const max = Number(await slider.getAttribute('max'));
    test.skip(max < 2, 'the chosen stack has fewer than three time steps');
    await slider.fill('2');
    await waitForState(page, (s) => s.t === 2);
    await page.locator('[data-testid="poles-image"]').click({ position: { x: 5, y: 5 } });
    await page.keyboard.press('ArrowLeft');
    await waitForState(page, (s) => s.t === 1);
    await page.keyboard.press('ArrowRight');
    await waitForState(page, (s) => s.t === 2);
  });

  test('the mode selector opens the instrument frames of the same stack', async ({ page }) => {
    await openApp(page);
    const chosen = await chooseStack(page);
    const before = await waitForState(page, (s) => s.stack_id === chosen && s.level !== null);
    expect(before.level).toBe('sequence');
    await expect(page.locator('[data-testid="stack-label"]')).toHaveText('Region snapshots');

    const frames = chosen.replace(/_sequence$/, '_frame');
    test.skip(!(await stackIds(page)).includes(frames), 'this mirror has no frame sibling');
    await page.locator('[data-testid="mode-frame"]').click();
    const after = await waitForState(page, (s) => s.level === 'frame');
    expect(after.stack_id).toBe(frames);
    expect(after.stack_id).not.toBe(before.stack_id);
    await expect(page.locator('[data-testid="stack-label"]')).toHaveText('Instrument frames');
  });

  test('the accumulating sweep reads out the sweep it is filling in', async ({ page }) => {
    await openApp(page);
    const chosen = await chooseStack(page);
    await waitForState(page, (s) => s.stack_id === chosen);

    const cumulative = chosen.replace(/_sequence$/, '_cumulative');
    test.skip(!(await stackIds(page)).includes(cumulative), 'this mirror has no cumulative sibling');
    await page.locator('[data-testid="mode-cumulative"]').click();
    const state = await waitForState(page, (s) => s.level === 'cumulative');
    expect(state.stack_id).toBe(cumulative);
    await expect(page.locator('[data-testid="time-readout"]')).toContainText('sweep');
    await expect(page.locator('[data-testid="stack-label"]')).toHaveText('Accumulating sweep');
    // The sweep counter follows the slider, not just the first step.
    await page.locator('[data-testid="time-slider"]').fill('5');
    await waitForState(page, (s) => s.t === 5);
    await expect(page.locator('[data-testid="time-readout"]')).toContainText(/sweep \d+, frame \d+ of \d+/);
    // No mode is missing here, so nothing offers to build one.
    await expect(page.locator('[data-testid="build-mode"]')).toHaveCount(0);
  });

  test('a rendered movie plays in a native video element', async ({ page }) => {
    await openApp(page);
    await page.locator('[data-testid="tab-poles"]').click();
    const select = page.locator('[data-testid="stack-select"]');
    await expect.poll(async () => select.locator('option').count(), { timeout: 90_000 }).toBeGreaterThan(1);
    const withMovie = await select.locator('option').evaluateAll((options) =>
      options
        .filter((option) => option.textContent?.includes('[movie]'))
        .map((option) => (option as HTMLOptionElement).value),
    );
    test.skip(withMovie.length === 0, 'no stack in this mirror has a movie');
    await select.selectOption(withMovie[0]);
    const video = page.locator('[data-testid="stack-movie"]');
    await expect(video).toBeVisible({ timeout: 60_000 });
    await expect
      .poll(async () => video.evaluate((element) => (element as HTMLVideoElement).readyState), {
        timeout: 90_000,
        intervals: [500],
      })
      .toBeGreaterThanOrEqual(1);
  });
});
