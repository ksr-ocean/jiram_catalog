import { expect, test } from '@playwright/test';
import { openApp, waitForState } from './helpers';

test('Coverage distinguishes archival metadata and forbidden pixels', async ({ page }) => {
  test.setTimeout(180_000);
  await openApp(page);
  await page.getByTestId('tab-coverage').click();
  await expect(page.getByTestId('coverage-table')).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId('coverage-view')).toContainText('Excluded');
  await expect(page.getByTestId('coverage-view')).toContainText('Unassessed');
  await page.getByRole('spinbutton', { name: 'Archive pass' }).fill('4');
  await expect(page.getByTestId('archive-table').locator('tbody tr').first()).toBeVisible();
  await page.getByTestId('archive-query').fill('THIS_OBSERVATION_DOES_NOT_EXIST');
  await expect(page.getByTestId('coverage-view')).toContainText('No archive records match');
});

test('selection collapse preserves contents; JunoCam filters match footprint summaries', async ({
  page,
}) => {
  await openApp(page);
  await page.getByTestId('filter-instrument').selectOption('JunoCam');
  await waitForState(page, (s) => s.instrument_filter === 'JunoCam' && s.n_filtered > 0);
  await page.getByTestId('add-page-to-selection').click();
  const before = await page.getByTestId('selection-count').textContent();
  await page.getByTestId('toggle-selection-tray').click();
  await expect(page.getByTestId('selection-tray')).toHaveCount(0);
  await expect(page.getByTestId('toggle-selection-tray')).toContainText(before!);
  await page.getByTestId('toggle-selection-tray').click();
  await expect(page.getByTestId('selection-count')).toHaveText(before!);
  await page.getByTestId('filter-lat-band').selectOption('N polar');
  await expect
    .poll(async () => {
      const text = (await page.getByTestId('table-caption').textContent()) ?? '';
      const nums = text.replaceAll(',', '').match(/\d+/g)?.map(Number);
      return Boolean(nums && nums[0] === nums[1]);
    })
    .toBe(true);
});

test('RGB export requires an explicit analysis band and preserves instrument-failure policy', async ({
  page,
}) => {
  test.setTimeout(180_000);
  await openApp(page);
  await page.getByTestId('tab-poles').click();
  const select = page.getByTestId('stack-select');
  await expect.poll(async () => select.locator('option').count()).toBeGreaterThan(1);
  const id = await select
    .locator('option')
    .evaluateAll((options) =>
      options
        .map((o) => (o as HTMLOptionElement).value)
        .find((v) => v.toLowerCase().includes('junocam')),
    );
  test.skip(!id, 'No eligible JunoCam stack is available');
  await select.selectOption(id!);
  await page.getByTestId('band-select').selectOption('__rgb');
  await expect(page.getByTestId('export-triples')).toBeDisabled();
  await expect(page.getByTestId('stack-readiness')).toContainText('Select a physical band');
  await page.getByTestId('analysis-band').selectOption('RED');
  await expect(page.getByTestId('stack-readiness')).toContainText('independent observations', {
    timeout: 120_000,
  });
  await expect(page.getByTestId('stack-readiness')).toContainText('Band RED');
});

test('Compare explains registration and exposes a reproducible recipe', async ({ page }) => {
  test.setTimeout(240_000);
  await openApp(page);
  await page.getByTestId('tab-compare').click();
  const view = page.getByTestId('compare-view');
  const sources = view.getByRole('combobox', { name: /source type$/ });
  await sources.nth(0).selectOption('strip');
  await sources.nth(1).selectOption('strip');
  await expect(sources.nth(0)).toHaveValue('strip');
  await expect(sources.nth(1)).toHaveValue('strip');
  const ids = await page
    .getByLabel('Left source', { exact: true })
    .locator('option')
    .evaluateAll((options) => options.map((o) => (o as HTMLOptionElement).value).filter(Boolean));
  test.skip(ids.length === 0, 'No library products available');
  await page.getByLabel('Left source', { exact: true }).selectOption(ids[0]);
  await page.getByLabel('Right source', { exact: true }).selectOption(ids[0]);
  for (const side of ['Left', 'Right']) {
    const bands = page.getByLabel(`${side} band`, { exact: true });
    if (await bands.count()) {
      const values = await bands
        .locator('option')
        .evaluateAll((options) =>
          options.map((o) => (o as HTMLOptionElement).value).filter(Boolean),
        );
      if (values.length) await bands.selectOption(values[0]);
    }
  }
  await expect(page.getByTestId('compare-diagnostics')).toBeVisible({ timeout: 180_000 });
  await expect(page.getByTestId('compare-diagnostics')).toContainText('Time separation');
  await expect(page.getByTestId('compare-image-0').getByTestId('frame-canvas')).toHaveAttribute(
    'data-loaded',
    'true',
  );
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download comparison and recipe' }).click();
  expect((await downloaded).suggestedFilename()).toBe('comparison_recipe.json');
});
