import { expect, test } from '@playwright/test';

import { apiDeleteShelvesByName, apiLogin, expectSignedIn, uniqueName } from '../helpers';

// Journey 25 — rename a shelf from the Shelves page and confirm the new name sticks in the list.
test('Journey 25 — rename a shelf', async ({ page, request }) => {
  const original = uniqueName('shelf-rename');
  const renamed = uniqueName('shelf-renamed');
  const token = await apiLogin(request);

  try {
    await page.goto('/#shelves');
    await expectSignedIn(page);

    // --- Create the shelf (auto-selected) ---
    await page.getByLabel('New shelf name').fill(original);
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.getByRole('heading', { name: original })).toBeVisible();

    // --- Rename it via the inline rename control ---
    await page.getByLabel('Rename shelf').fill(renamed);
    await page.getByRole('button', { name: 'Rename', exact: true }).click();
    await expect(page.getByText('Shelf renamed', { exact: true })).toBeVisible();

    // --- The new name shows in the detail head and the left-hand list ---
    await expect(page.getByRole('heading', { name: renamed })).toBeVisible();
    await expect(page.getByRole('button', { name: original })).toHaveCount(0);
  } finally {
    await apiDeleteShelvesByName(request, token, original);
    await apiDeleteShelvesByName(request, token, renamed);
  }
});
