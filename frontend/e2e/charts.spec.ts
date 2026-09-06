import { expect, test } from '@playwright/test';
import { openApp } from './helpers';

test('the coverage charts render from the server summary', async ({ page }) => {
  await openApp(page);
  await expect(page.locator('[data-testid="chart-lat-band"] .js-plotly-plot')).toBeVisible({ timeout: 120_000 });
  await expect(page.locator('[data-testid="chart-orbit"] .js-plotly-plot')).toBeVisible();
  await expect(page.locator('[data-testid="chart-month"] .js-plotly-plot')).toBeVisible();
});
