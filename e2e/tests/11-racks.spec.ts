import { expect, test } from '@playwright/test';

import {
  API_URL,
  apiArchiveRacksByName,
  apiArchiveShelvesByName,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../helpers';

// Journey 11 — create a rack and add a shelf to it. The shelf is created up front via the API so
// the spec focuses on the Racks UI; membership is asserted, then everything is cleaned up.
test('Journey 11 — create a rack and add a shelf to it', async ({ page, request }) => {
  const rackName = uniqueName('rack');
  const shelfName = uniqueName('rack-shelf');
  const token = await apiLogin(request);

  // Create the shelf via the API (it becomes selectable in the Racks "Add a shelf" picker).
  const shelfRes = await request.post(`${API_URL}/api/v1/shelves`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: shelfName, access_level: 'open' },
  });
  expect(shelfRes.ok(), `createShelf failed: ${shelfRes.status()}`).toBeTruthy();

  try {
    await page.goto('/#racks');
    await expectSignedIn(page);

    // --- Create the rack (auto-selected after creation) ---
    await page.getByLabel('New rack name').fill(rackName);
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.getByRole('heading', { name: rackName })).toBeVisible();

    // --- Add the shelf to the rack ---
    await page.getByLabel('Choose a shelf').selectOption({ label: shelfName });
    await page.getByRole('button', { name: 'Add shelf' }).click();

    // --- Assert membership ---
    await expect(page.getByRole('heading', { name: /Shelves in this rack \(1\)/ })).toBeVisible();
    await expect(page.getByText(shelfName, { exact: false })).toBeVisible();
  } finally {
    await apiArchiveRacksByName(request, token, rackName);
    await apiArchiveShelvesByName(request, token, shelfName);
  }
});
