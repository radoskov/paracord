import { expect, test } from '@playwright/test';

import {
  apiArchiveShelvesByName,
  apiCreateWork,
  apiDeleteWorksByTitle,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../helpers';

test('Journey 3 — create a shelf, add a paper, assert membership, clean up', async ({
  page,
  request,
}) => {
  const shelfName = uniqueName('shelf');
  const paperTitle = uniqueName('shelf-paper');
  const token = await apiLogin(request);
  // Create the paper up front via the API so this spec focuses on the shelves UI.
  await apiCreateWork(request, token, paperTitle);

  try {
    await page.goto('/#shelves');
    await expectSignedIn(page);

    // --- Create the shelf (auto-selected after creation) ---
    await page.getByLabel('New shelf name').fill(shelfName);
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.getByRole('heading', { name: shelfName })).toBeVisible();

    // --- Add the paper: filter to it first so it's guaranteed in the (capped) picker ---
    await page.getByLabel('Filter papers', { exact: true }).fill(paperTitle);
    await page.getByLabel('Choose a paper').selectOption({ label: paperTitle });
    await page.getByRole('button', { name: 'Add paper' }).click();

    // --- Assert membership ---
    await expect(page.getByRole('heading', { name: /Papers in this shelf \(1\)/ })).toBeVisible();
    await expect(page.getByText(paperTitle, { exact: false })).toBeVisible();
  } finally {
    await apiArchiveShelvesByName(request, token, shelfName);
    await apiDeleteWorksByTitle(request, token, paperTitle);
  }
});
