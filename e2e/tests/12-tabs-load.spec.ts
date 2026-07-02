import { expect, test } from '@playwright/test';

import { expectSignedIn } from '../helpers';

// Journey 12 — read-only smoke: the Duplicates and Insights tabs load and render without error.
test('Journey 12 — Duplicates and Insights tabs load without error', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));

  await page.goto('/#duplicates');
  await expectSignedIn(page);
  await expect(page.getByRole('heading', { name: 'Duplicate & version review' })).toBeVisible();

  await page.goto('/#insights');
  await expect(page.getByRole('heading', { name: 'Scope', exact: true })).toBeVisible();

  expect(errors, `uncaught page errors: ${errors.join(' | ')}`).toEqual([]);
});
