import { expect, test } from '@playwright/test';

import { expectSignedIn } from '../helpers';

test('Journey 6 — tab navigation (click + arrow keys) and cached tab state', async ({ page }) => {
  await page.goto('/#library');
  await expectSignedIn(page);

  // --- Click navigation ---
  await page.getByRole('link', { name: 'Search' }).click();
  await expect(page).toHaveURL(/#search$/);
  await expect(page.getByRole('heading', { name: 'Search' })).toBeVisible();

  // --- Arrow-key navigation (focus must not be in an input) ---
  await page.goto('/#library');
  await page.getByRole('heading', { name: 'PaRacORD' }).click();
  await page.keyboard.press('ArrowRight');
  await expect(page).toHaveURL(/#search$/);

  // --- Cached state: a typed query survives leaving and returning to the tab (#9) ---
  const probe = 'persist-check-123';
  await page.getByLabel('Search query').fill(probe);
  await page.getByRole('link', { name: 'Library' }).click();
  await expect(page).toHaveURL(/#library$/);
  await page.getByRole('link', { name: 'Search' }).click();
  await expect(page.getByLabel('Search query')).toHaveValue(probe);
});
