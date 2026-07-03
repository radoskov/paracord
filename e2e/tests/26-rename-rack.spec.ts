import { expect, test } from '@playwright/test';

import { apiArchiveRacksByName, apiLogin, expectSignedIn, uniqueName } from '../helpers';

// Journey 26 — rename a rack from the Racks page and confirm the new name sticks in the list.
test('Journey 26 — rename a rack', async ({ page, request }) => {
  const original = uniqueName('rack-rename');
  const renamed = uniqueName('rack-renamed');
  const token = await apiLogin(request);

  try {
    await page.goto('/#racks');
    await expectSignedIn(page);

    // --- Create the rack (auto-selected) ---
    await page.getByLabel('New rack name').fill(original);
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.getByRole('heading', { name: original })).toBeVisible();

    // --- Rename it via the inline rename control ---
    await page.getByLabel('Rename rack').fill(renamed);
    await page.getByRole('button', { name: 'Rename', exact: true }).click();
    await expect(page.getByText('Rack renamed', { exact: true })).toBeVisible();

    // --- The new name shows in the detail head and the left-hand list ---
    await expect(page.getByRole('heading', { name: renamed })).toBeVisible();
    await expect(page.getByRole('button', { name: original })).toHaveCount(0);
  } finally {
    // Racks have no hard-delete helper; archive both possible names to keep active lists clean.
    await apiArchiveRacksByName(request, token, original);
    await apiArchiveRacksByName(request, token, renamed);
  }
});
