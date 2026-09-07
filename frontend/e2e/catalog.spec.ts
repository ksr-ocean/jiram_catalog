import { expect, test } from '@playwright/test';
import { captionCounts, centreOf, debugState, openApp, waitForState } from './helpers';

test.describe('catalog view', () => {
  test('draws the whole on-planet catalog', async ({ page }) => {
    await openApp(page);
    const state = await debugState(page);
    expect(state.n_points).toBeGreaterThanOrEqual(40_000);
    // Everything passes the default filters except a JunoCam image of tier C,
    // which the contract hides unless it is asked for; on a mirror with no
    // JunoCam rows at all the two counts are still equal.
    expect(state.n_filtered).toBeLessThanOrEqual(state.n_points);
    expect(state.n_filtered).toBeGreaterThanOrEqual(40_000);
    expect(state.instrument_filter).toBe('all');
    expect(state.view).toBe('cyl');
    await expect(page.locator('[data-testid="catalog-map"] canvas')).toBeVisible();
    await expect(page.locator('[data-testid="config-meta"]')).toContainText('frames on planet');
  });

  test('a filter reduces the drawn set, and the server agrees', async ({ page }) => {
    await openApp(page);
    const before = (await debugState(page)).n_filtered;
    await page.locator('[data-testid="filter-half"]').selectOption('M');
    const after = await waitForState(page, (s) => s.n_filtered < before);
    expect(after.n_filtered).toBeLessThan(before);
    expect(after.n_filtered).toBeGreaterThan(0);
    // The caption carries the server's own count for the same filters, which
    // arrives on its own round trip, so it is polled rather than read once.
    await expect(page.locator('[data-testid="table-caption"]')).toContainText('server agrees', {
      timeout: 60_000,
    });
    await expect
      .poll(async () => (await captionCounts(page))?.[1], { timeout: 60_000, intervals: [400] })
      .toBe(after.n_filtered);
    expect((await captionCounts(page))?.[0]).toBe(after.n_filtered);
  });

  test('hovering a dense area shows a tooltip with a product id', async ({ page }, testInfo) => {
    await openApp(page);
    // Narrow to the north polar band and fit the view to it, so the middle of
    // the canvas is somewhere the spacecraft actually looked.
    await page.locator('[data-testid="filter-lat-band"]').selectOption('N polar');
    await waitForState(page, (s) => s.n_filtered > 0 && s.n_filtered < 47_000);
    await page.locator('[data-testid="tool-pan"]').click();
    await page.locator('[data-testid="zoom-to-data"]').click();
    await page.waitForTimeout(500);

    const map = page.locator('[data-testid="catalog-map"]');
    const box = await centreOf(map);
    const tooltip = page.locator('[data-testid="catalog-map"] .tooltip');
    let shown = false;
    for (const [dx, dy] of [[0, 0], [8, 0], [-8, 6], [0, -12], [20, 20], [-30, -10], [40, 0], [0, 40]]) {
      await page.mouse.move(box.x + dx, box.y + dy);
      await page.waitForTimeout(250);
      if (await tooltip.isVisible().catch(() => false)) {
        shown = true;
        break;
      }
    }
    expect(shown, 'a tooltip appeared while hovering the point cloud').toBe(true);
    const productId = await page.locator('[data-testid="tooltip-product-id"]').textContent();
    expect(productId?.trim().length ?? 0).toBeGreaterThan(3);
    testInfo.annotations.push({
      type: 'picking',
      description: `tooltip came from ${await tooltip.getAttribute('data-source')} picking`,
    });
  });

  test('a box selection fills the tray', async ({ page }) => {
    await openApp(page);
    expect((await debugState(page)).selection_n).toBe(0);
    await page.locator('[data-testid="tool-box"]').click();
    const map = page.locator('[data-testid="catalog-map"]');
    const box = await centreOf(map);
    await page.mouse.move(box.x - box.width * 0.3, box.y - box.height * 0.3);
    await page.mouse.down();
    await page.mouse.move(box.x, box.y, { steps: 8 });
    await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3, { steps: 8 });
    await page.mouse.up();
    const after = await waitForState(page, (s) => s.selection_n > 0);
    expect(after.selection_n).toBeGreaterThan(0);
    await expect(page.locator('[data-testid="selection-count"]')).not.toHaveText('0');
    // Clearing puts it back, so the tray is a live view of the same state.
    await page.locator('[data-testid="clear-selection"]').click();
    await waitForState(page, (s) => s.selection_n === 0);
  });

  test('the polar views project without losing the point set', async ({ page }) => {
    await openApp(page);
    const total = (await debugState(page)).n_points;
    await page.locator('[data-testid="view-mode-N"]').click();
    const north = await waitForState(page, (s) => s.view === 'N');
    expect(north.n_filtered).toBe(total);
    await page.locator('[data-testid="view-mode-cyl"]').click();
    await waitForState(page, (s) => s.view === 'cyl');
  });
});
