import { expect, test } from '@playwright/test';
import { openApp } from './helpers';

test.describe('strips view', () => {
  test('lists strips, opens one, and plots its statistics', async ({ page }) => {
    await openApp(page);
    await page.locator('[data-testid="tab-strips"]').click();
    const rows = page.locator('[data-testid="strips-table"] tbody tr');
    await expect.poll(async () => rows.count(), { timeout: 120_000 }).toBeGreaterThan(0);
    await expect(page.locator('[data-testid="strip-map"] canvas')).toBeVisible();

    await rows.first().click();
    await expect(page.locator('[data-testid="current-strip"]')).toBeVisible({ timeout: 90_000 });
    await expect(page.locator('[data-testid="strip-image"] canvas').first()).toBeVisible({ timeout: 120_000 });

    // The statistics are computed on the server and cached; give them room.
    await expect(page.locator('.js-plotly-plot').first()).toBeVisible({ timeout: 180_000 });
    await expect(page.locator('[data-testid="plot-isotropic"] .js-plotly-plot')).toBeVisible();
    await expect(page.locator('[data-testid="download-stats"]')).toBeEnabled();
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
