import { expect, test } from '@playwright/test';

import {
  API_URL,
  apiArchiveShelvesByName,
  apiCreateShelf,
  apiDeleteShelvesByName,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../helpers';

// Journey 20 — rack lifecycle: add two shelves to a rack, remove one, then delete the rack while
// KEEPING its shelves, and confirm both shelves survive (domain rule: deleting a rack never deletes
// the shelves unless the operator opts in). The rack is created via the UI; the shelves via the API
// so the spec focuses on the rack membership + deletion flow.
test('Journey 20 — add/remove shelves on a rack, then delete the rack keeping its shelves', async ({
  page,
  request,
}) => {
  const rackName = uniqueName('rack-life');
  const shelfA = uniqueName('rack-shelf-a');
  const shelfB = uniqueName('rack-shelf-b');
  const token = await apiLogin(request);

  await apiCreateShelf(request, token, shelfA);
  await apiCreateShelf(request, token, shelfB);

  // Delete rack asks two confirms: "Delete rack?" then, if it holds shelves, "also DELETE them?".
  // Accept the first, DISMISS the second so the shelves are kept (leave the rack only).
  page.on('dialog', (d) => {
    if (/also DELETE/i.test(d.message())) return void d.dismiss();
    return void d.accept();
  });

  try {
    await page.goto('/#racks');
    await expectSignedIn(page);

    // --- Create the rack (auto-selected) ---
    await page.getByLabel('New rack name').fill(rackName);
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.getByRole('heading', { name: rackName })).toBeVisible();

    // --- Add both shelves to the rack ---
    await page.getByLabel('Choose a shelf').selectOption({ label: shelfA });
    await page.getByRole('button', { name: 'Add shelf' }).click();
    await expect(page.getByRole('heading', { name: /Shelves in this rack \(1\)/ })).toBeVisible();
    await page.getByLabel('Choose a shelf').selectOption({ label: shelfB });
    await page.getByRole('button', { name: 'Add shelf' }).click();
    await expect(page.getByRole('heading', { name: /Shelves in this rack \(2\)/ })).toBeVisible();

    // --- Remove shelf A from the rack (shelf itself survives) ---
    const rowA = page.getByRole('listitem').filter({ hasText: shelfA });
    await rowA.getByRole('button', { name: 'Remove' }).click();
    await expect(page.getByRole('heading', { name: /Shelves in this rack \(1\)/ })).toBeVisible();
    await expect(page.getByText('Shelf removed', { exact: true })).toBeVisible();

    // --- Delete the rack, keeping its remaining shelf (dismiss the "also delete" confirm) ---
    await page.getByRole('button', { name: 'Delete rack' }).click();
    await expect(page.getByText('Rack deleted', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: rackName })).toHaveCount(0);

    // --- Both shelves still exist (the rack deletion left them alone) ---
    const shelvesRes = await request.get(`${API_URL}/api/v1/shelves`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const names = ((await shelvesRes.json()) as Array<{ name: string }>).map((s) => s.name);
    expect(names).toContain(shelfA);
    expect(names).toContain(shelfB);
  } finally {
    await apiDeleteShelvesByName(request, token, shelfA);
    await apiDeleteShelvesByName(request, token, shelfB);
    await apiArchiveShelvesByName(request, token, shelfA);
    await apiArchiveShelvesByName(request, token, shelfB);
  }
});
