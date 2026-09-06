import { expect, test } from '@playwright/test';
import { debugState, openApp } from './helpers';

/** The height of the viewer's WebGL canvas, in CSS pixels. */
async function viewerHeight(page: import('@playwright/test').Page): Promise<number> {
  const box = await page.locator('[data-testid="strip-image"] canvas').first().boundingBox();
  if (!box) throw new Error('the strip viewer has no box');
  return box.height;
}

test.describe('strips view', () => {
  test('lists strips, opens one, and plots its statistics behind the toggle', async ({ page }) => {
    await openApp(page);
    await page.locator('[data-testid="tab-strips"]').click();
    const rows = page.locator('[data-testid="strips-table"] tbody tr');
    await expect.poll(async () => rows.count(), { timeout: 120_000 }).toBeGreaterThan(0);
    await expect(page.locator('[data-testid="strip-map"] canvas')).toBeVisible();

    await rows.first().click();
    await expect(page.locator('[data-testid="current-strip"]')).toBeVisible({ timeout: 90_000 });
    await expect(page.locator('[data-testid="strip-image"] canvas').first()).toBeVisible({ timeout: 120_000 });

    // The statistics start hidden, and the viewer has the panel's space.
    expect((await debugState(page)).stats_visible).toBe(false);
    await expect(page.locator('[data-testid="strip-stats"]')).toHaveCount(0);
    const tallHeight = await viewerHeight(page);

    await page.locator('[data-testid="toggle-stats"]').click();
    await expect.poll(async () => (await debugState(page)).stats_visible).toBe(true);

    // The statistics are computed on the server and cached; give them room.
    await expect(page.locator('.js-plotly-plot').first()).toBeVisible({ timeout: 180_000 });
    await expect(page.locator('[data-testid="plot-isotropic"] .js-plotly-plot')).toBeVisible();
    await expect(page.locator('[data-testid="download-stats"]')).toBeEnabled();

    // ...and the viewer gave that space back.
    const shortHeight = await viewerHeight(page);
    expect(tallHeight).toBeGreaterThan(shortHeight);
  });

  test('the tray sends its orbits to the strips filter', async ({ page }) => {
    await openApp(page);
    await page.locator('[data-testid="select-all-filtered"]').click();
    await expect(page.locator('[data-testid="selection-count"]')).not.toHaveText('0');
    await page.locator('[data-testid="show-in-strips"]').click();
    await expect(page.locator('[data-testid="view-strips"]')).toBeVisible();
    await expect(page.locator('[data-testid="strip-orbit-filter"]')).toContainText('orbits');
  });
});
