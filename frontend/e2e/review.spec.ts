/** Lead acceptance gate for the accepted review. Executors: read-only. */
import { expect, test } from '@playwright/test';
import { openApp, waitForState } from './helpers';

test('JunoCam has dates and details; desktop statistics retain their image', async ({ page }) => {
  await page.setViewportSize({width:1366,height:768});
  await openApp(page);
  await expect(page.getByTestId('tab-catalog')).toContainText('Explore');
  await expect(page.getByTestId('tab-poles')).toContainText('Time series');
  await expect(page.getByTestId('tab-strips')).toContainText('Image library');
  const instrument=page.getByTestId('filter-instrument');
  await instrument.selectOption('JunoCam');
  await waitForState(page,s=>s.instrument_filter==='JunoCam' && s.n_filtered>0);
  const row=page.locator('[data-testid=catalog-table] tbody tr').first();
  await expect(row).not.toContainText('1970-01-01');
  await row.locator('a').first().click();
  await expect(page.getByTestId('frame-detail')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByTestId('filter-half')).toHaveCount(0);
  await expect(page.getByTestId('filter-revisit')).toHaveCount(0);
  await page.getByTestId('tab-strips').click();
  await page.getByTestId('strip-instrument').selectOption('JunoCam');
  await page.locator('[data-testid=strips-table] tbody tr').first().click();
  await page.getByRole('button',{name:'Show statistics',exact:true}).click();
  const view=page.getByTestId('strip-image');
  await expect.poll(async()=> (await view.boundingBox())?.height??0).toBeGreaterThanOrEqual(300);
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
  expect(overflow).toBe(false);
});
