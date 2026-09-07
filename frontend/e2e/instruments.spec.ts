/**
 * The JunoCam half of the catalog: instruments, footprints, bands, composites.
 *
 * Every test here skips when `/api/config` reports no JunoCam images, so the
 * suite is green on a mirror that has only run the JIRAM side.  That is not a
 * courtesy: the gate runs this file against whatever the mirror holds, and a
 * test that fails for want of data says nothing about the code.
 */
import { expect, test, type Page } from '@playwright/test';
import {
  captionCounts,
  centreOf,
  debugState,
  frameCanvasStats,
  junocamCount,
  openApp,
  waitForState,
} from './helpers';

/** Skip the rest of a test unless the backend has JunoCam rows. */
async function requireJunocam(page: Page): Promise<void> {
  const count = await junocamCount(page);
  test.skip(count === 0, 'this backend reports no JunoCam images');
}

/** Every stack id the chooser offers. */
async function stackIds(page: Page): Promise<string[]> {
  return page
    .locator('[data-testid="stack-select"] option')
    .evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value).filter(Boolean));
}

test.describe('instruments in the catalog', () => {
  test('the instrument filter changes the drawn set and draws footprints', async ({ page }) => {
    await openApp(page);
    await requireJunocam(page);
    const before = await debugState(page);
    expect(before.instrument_filter).toBe('all');

    await page.locator('[data-testid="filter-instrument"]').selectOption('JunoCam');
    const junocam = await waitForState(page, (s) => s.instrument_filter === 'JunoCam' && s.n_filtered > 0);
    expect(junocam.n_filtered).toBeLessThan(before.n_filtered);
    // A JunoCam row is a swath, so every drawn row has an outline.
    expect(junocam.n_footprints).toBeGreaterThan(0);
    // The server counts the same rows for the same filters, once its own
    // round trip has landed.
    await expect
      .poll(async () => (await captionCounts(page))?.[1], { timeout: 60_000, intervals: [400] })
      .toBe(junocam.n_filtered);
    expect((await captionCounts(page))?.[0]).toBe(junocam.n_filtered);

    // JIRAM rows are points and draw no outlines at all.
    await page.locator('[data-testid="filter-instrument"]').selectOption('JIRAM');
    const jiram = await waitForState(page, (s) => s.instrument_filter === 'JIRAM');
    expect(jiram.n_footprints).toBe(0);
    expect(jiram.n_filtered).toBeGreaterThan(junocam.n_filtered);
  });

  test('the band filter follows the instrument', async ({ page }) => {
    await openApp(page);
    await requireJunocam(page);
    await page.locator('[data-testid="filter-instrument"]').selectOption('JunoCam');
    await waitForState(page, (s) => s.instrument_filter === 'JunoCam');
    const options = await page
      .locator('[data-testid="filter-band"] option')
      .evaluateAll((all) => all.map((option) => (option as HTMLOptionElement).value));
    expect(options).toContain('RED');
    expect(options).not.toContain('M');

    const before = (await debugState(page)).n_filtered;
    await page.locator('[data-testid="filter-band"]').selectOption('RED');
    const after = await waitForState(page, (s) => s.n_filtered > 0);
    expect(after.n_filtered).toBeLessThanOrEqual(before);
  });

  test('hovering a footprint shows a tooltip with the product id', async ({ page }) => {
    await openApp(page);
    await requireJunocam(page);
    await page.locator('[data-testid="filter-instrument"]').selectOption('JunoCam');
    await waitForState(page, (s) => s.instrument_filter === 'JunoCam' && s.n_footprints > 0);
    // Fit the view to the outlines, so the middle of the canvas is inside one.
    await page.locator('[data-testid="tool-pan"]').click();
    await page.locator('[data-testid="zoom-to-data"]').click();
    await page.waitForTimeout(600);

    const map = page.locator('[data-testid="catalog-map"]');
    const box = await centreOf(map);
    const tooltip = page.locator('[data-testid="catalog-map"] .tooltip');
    // A swath is a large target but the outlines do not tile the map, so the
    // pointer walks a grid over it rather than guessing at the middle.
    let shown = false;
    const fractions = [-0.35, -0.175, 0, 0.175, 0.35];
    outer: for (const fy of fractions) {
      for (const fx of fractions) {
        await page.mouse.move(box.x + fx * box.width, box.y + fy * box.height);
        await page.waitForTimeout(160);
        if (await tooltip.isVisible().catch(() => false)) {
          shown = true;
          break outer;
        }
      }
    }
    expect(shown, 'a tooltip appeared over a JunoCam footprint').toBe(true);
    const productId = await page.locator('[data-testid="tooltip-product-id"]').textContent();
    expect(productId?.trim().length ?? 0).toBeGreaterThan(3);
    await expect(page.locator('[data-testid="tooltip-instrument"]')).toContainText('JunoCam');
  });

  test('colouring by instrument names both in the legend', async ({ page }) => {
    await openApp(page);
    await requireJunocam(page);
    await page.locator('[data-testid="color-by"]').selectOption('instrument');
    await expect(page.locator('[data-testid="catalog-legend"]')).toContainText('JunoCam');
    await expect(page.locator('[data-testid="catalog-legend"]')).toContainText('JIRAM');
  });
});

test.describe('bands and composites in the Poles viewer', () => {
  test('a JunoCam stack offers its bands and an RGB composite', async ({ page }, testInfo) => {
    await openApp(page);
    await requireJunocam(page);
    await page.locator('[data-testid="tab-poles"]').click();
    const select = page.locator('[data-testid="stack-select"]');
    await expect.poll(async () => select.locator('option').count(), { timeout: 90_000 }).toBeGreaterThan(1);
    const junocam = (await stackIds(page)).filter((id) => id.toLowerCase().includes('junocam'));
    test.skip(junocam.length === 0, 'this mirror has no JunoCam stack');

    await select.selectOption(junocam[0]);
    const opened = await waitForState(page, (s) => s.stack_id === junocam[0] && s.band !== null);
    expect(opened.composite).toBe(false);
    await expect(page.locator('[data-testid="band-select"]')).toBeVisible();
    await expect(page.locator('[data-testid="stack-instrument"]')).toHaveText('JunoCam');
    // JunoCam images are whole swaths, so the other two levels cannot exist.
    await expect(page.locator('[data-testid="mode-frame"]')).toBeVisible();
    await expect(page.locator('[data-testid="mode-sequence"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="mode-cumulative"]')).toHaveCount(0);

    await expect(page.locator('[data-testid="frame-canvas"][data-loaded="true"]').first()).toBeAttached({
      timeout: 90_000,
    });
    const single = await frameCanvasStats(page);
    expect(single?.visible ?? 0, 'the single-band frame has visible pixels').toBeGreaterThan(0);
    // One band through the grey lookup table is grey by construction.
    expect(single?.nonGray, 'a grey-mapped single band has no coloured pixels').toBe(0);

    const bands = await page
      .locator('[data-testid="band-select"] option')
      .evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value));
    test.skip(!bands.includes('__rgb'), 'this stack does not carry RED, GREEN and BLUE');

    await page.locator('[data-testid="band-select"]').selectOption('__rgb');
    await waitForState(page, (s) => s.composite === true);
    await expect(page.locator('[data-testid="composite-note"]')).toContainText('RGB composite');
    // The composite is not colour-mapped, and `data-composite` describes the
    // pixels on the canvas rather than the mode, so waiting on it waits for
    // the composite itself to arrive rather than for the click to register.
    await expect
      .poll(async () => page.locator('[data-testid="frame-canvas"]').first().getAttribute('data-composite'), {
        timeout: 60_000,
      })
      .toBe('true');
    await expect(page.locator('[data-testid="cmap-select"]')).toBeDisabled();
    const composite = await frameCanvasStats(page);
    expect(composite?.visible ?? 0).toBeGreaterThan(0);
    expect(composite?.signature, 'the composite differs from the single band it replaced').not.toBe(
      single?.signature,
    );
    testInfo.annotations.push({
      type: 'composite',
      description: `${composite?.nonGray ?? 0} of ${composite?.visible ?? 0} sampled pixels carry colour`,
    });

    // Unlinking the bands puts a stretch pair per channel on screen.
    await page.locator('[data-testid="link-bands"]').uncheck();
    await expect(page.locator('[data-testid="band-stretch"]')).toBeVisible();
    await expect(page.locator('[data-testid="stretch-g-min"]')).toBeVisible();

    // ...and going back to one band brings the colour maps back with it.
    await page.locator('[data-testid="band-select"]').selectOption(bands[0]);
    const back = await waitForState(page, (s) => s.composite === false);
    expect(back.band).toBe(bands[0]);
    await expect(page.locator('[data-testid="cmap-select"]')).toBeEnabled();
  });
});

test.describe('illumination normalisation in the Poles viewer', () => {
  test('a JunoCam stack opens Lambert-corrected and the selector changes the picture', async ({
    page,
  }, testInfo) => {
    await openApp(page);
    await requireJunocam(page);
    await page.locator('[data-testid="tab-poles"]').click();
    const select = page.locator('[data-testid="stack-select"]');
    await expect.poll(async () => select.locator('option').count(), { timeout: 90_000 }).toBeGreaterThan(1);
    const junocam = (await stackIds(page)).filter((id) => id.toLowerCase().includes('junocam'));
    test.skip(junocam.length === 0, 'this mirror has no JunoCam stack');

    await select.selectOption(junocam[0]);
    // A reflected-light instrument opens divided by cos(i): limb darkening is
    // the loudest thing in the picture and it is not a property of Jupiter.
    const opened = await waitForState(page, (s) => s.stack_id === junocam[0] && s.band !== null);
    expect(opened.norm).toBe('lambert');
    expect(opened.stretch_mode).toBe('linear');
    await expect(page.locator('[data-testid="norm-select"]')).toHaveValue('lambert');

    await expect(page.locator('[data-testid="frame-canvas"][data-loaded="true"]').first()).toBeAttached({
      timeout: 90_000,
    });
    const corrected = await frameCanvasStats(page);
    expect(corrected?.visible ?? 0, 'the corrected frame has visible pixels').toBeGreaterThan(0);

    // Turning the model off is a different picture of the same numbers.
    await page.locator('[data-testid="norm-select"]').selectOption('none');
    await waitForState(page, (s) => s.norm === 'none');
    await expect
      .poll(async () => (await frameCanvasStats(page))?.signature, { timeout: 90_000, intervals: [500] })
      .not.toBe(corrected?.signature);
    const raw = await frameCanvasStats(page);
    expect(raw?.visible ?? 0).toBeGreaterThan(0);
    testInfo.annotations.push({
      type: 'illumination',
      description: `raw signature ${raw?.signature} vs lambert ${corrected?.signature}`,
    });

    // A parameterised model carries its parameter in the state and on the wire.
    const request = page.waitForRequest(
      (r) => r.url().includes('/frame/') && r.url().includes('norm=minnaert'),
      { timeout: 90_000 },
    );
    await page.locator('[data-testid="norm-select"]').selectOption('minnaert');
    await expect(page.locator('[data-testid="norm-k"]')).toBeVisible();
    const asked = await request;
    expect(decodeURIComponent(asked.url())).toContain('norm=minnaert:');
    expect((await debugState(page)).norm).toMatch(/^minnaert:/);

    // ...and the stretch mapping is its own control.
    await page.locator('[data-testid="stretch-mode"]').selectOption('asinh');
    await waitForState(page, (s) => s.stretch_mode === 'asinh');
  });
});

test.describe('bands in the Strips viewer', () => {
  test('a JunoCam strip computes its statistics for the chosen band', async ({ page }) => {
    await openApp(page);
    await requireJunocam(page);
    await page.locator('[data-testid="tab-strips"]').click();
    const rows = page.locator('[data-testid="strips-table"] tbody tr');
    await expect.poll(async () => rows.count(), { timeout: 120_000 }).toBeGreaterThan(0);

    const instrumentFilter = page.locator('[data-testid="strip-instrument"]');
    test.skip((await instrumentFilter.count()) === 0, 'this mirror has no JunoCam strips');
    await instrumentFilter.selectOption('JunoCam');
    await expect.poll(async () => rows.count(), { timeout: 60_000 }).toBeGreaterThan(0);

    await rows.first().click();
    await expect(page.locator('[data-testid="current-strip"]')).toBeVisible({ timeout: 90_000 });
    const bandSelect = page.locator('[data-testid="strip-band-select"]');
    await expect(bandSelect).toBeVisible({ timeout: 60_000 });
    const bands = await bandSelect
      .locator('option')
      .evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value));
    test.skip(!bands.includes('GREEN'), 'this strip has no GREEN band');

    // The statistics request carries the illumination model: a spectrum of a
    // limb-darkened swath and a spectrum of the corrected one are two
    // different measurements and must not share a label.
    const statsRequest = page.waitForRequest(
      (r) => r.url().includes('/stats') && r.url().includes('norm='),
      { timeout: 120_000 },
    );
    await bandSelect.selectOption('GREEN');
    await waitForState(page, (s) => s.strip_band === 'GREEN');
    expect((await statsRequest).url()).toContain('band=GREEN');
    expect((await debugState(page)).strip_norm).toBe('lambert');
    await expect(page.locator('[data-testid="strip-image"] canvas').first()).toBeVisible({ timeout: 120_000 });

    if (!(await debugState(page)).stats_visible) {
      await page.locator('[data-testid="toggle-stats"]').click();
    }
    await expect.poll(async () => (await debugState(page)).stats_visible).toBe(true);
    // The statistics are computed for the band on screen, and say so.
    await expect(page.locator('[data-testid="plot-isotropic"] .js-plotly-plot')).toBeVisible({ timeout: 180_000 });
    await expect(page.locator('[data-testid="strip-stats"]')).toContainText('GREEN');
    await expect(page.locator('[data-testid="download-stats"]')).toBeEnabled();

    // Changing the model asks for that model's statistics.
    const raw = page.waitForRequest(
      (r) => r.url().includes('/stats') && r.url().includes('norm=none'),
      { timeout: 120_000 },
    );
    await page.locator('[data-testid="strip-norm-select"]').selectOption('none');
    await raw;
    await waitForState(page, (s) => s.strip_norm === 'none');
  });

  test('a JunoCam strip composes RGB in the browser', async ({ page }) => {
    await openApp(page);
    await requireJunocam(page);
    await page.locator('[data-testid="tab-strips"]').click();
    const rows = page.locator('[data-testid="strips-table"] tbody tr');
    await expect.poll(async () => rows.count(), { timeout: 120_000 }).toBeGreaterThan(0);
    const instrumentFilter = page.locator('[data-testid="strip-instrument"]');
    test.skip((await instrumentFilter.count()) === 0, 'this mirror has no JunoCam strips');
    await instrumentFilter.selectOption('JunoCam');
    await expect.poll(async () => rows.count(), { timeout: 60_000 }).toBeGreaterThan(0);
    await rows.first().click();
    const bandSelect = page.locator('[data-testid="strip-band-select"]');
    await expect(bandSelect).toBeVisible({ timeout: 90_000 });
    const bands = await bandSelect
      .locator('option')
      .evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value));
    test.skip(!bands.includes('__rgb'), 'this strip does not carry RED, GREEN and BLUE');

    await bandSelect.selectOption('__rgb');
    await waitForState(page, (s) => s.strip_composite === true);
    await expect(page.locator('[data-testid="strip-composite-note"]')).toContainText('RGB composite');
    await expect(page.locator('[data-testid="strip-cmap-select"]')).toBeDisabled();
  });
});
