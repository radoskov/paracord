import { expect, test } from '@playwright/test';

import {
  API_URL,
  apiArchiveRacksByName,
  apiCreateRack,
  apiDeleteRowsByName,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../helpers';

// Journey 40 — row lifecycle (Rows = the broadest grouping layer; a row contains racks). Add two
// racks to a row, remove one, then delete the row while KEEPING its racks, and confirm both racks
// survive (domain rule: deleting a row never deletes the racks unless the operator opts in). The row
// is created via the UI; the racks via the API so the spec focuses on the row membership + deletion.
test('Journey 40 — add/remove racks on a row, then delete the row keeping its racks', async ({
  page,
  request,
}) => {
  const rowName = uniqueName('row-life');
  const rackA = uniqueName('row-rack-a');
  const rackB = uniqueName('row-rack-b');
  const token = await apiLogin(request);

  await apiCreateRack(request, token, rackA);
  await apiCreateRack(request, token, rackB);

  // Delete row asks two confirms: "Delete row?" then, if it holds racks, "also DELETE them?".
  // Accept the first, DISMISS the second so the racks are kept (leave the row only).
  page.on('dialog', (d) => {
    if (/also DELETE/i.test(d.message())) return void d.dismiss();
    return void d.accept();
  });

  try {
    await page.goto('/#rows');
    await expectSignedIn(page);

    // --- Create the row (auto-selected) ---
    await page.getByLabel('New row name').fill(rowName);
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.getByRole('heading', { name: rowName })).toBeVisible();

    // --- Add both racks to the row ---
    await page.getByLabel('Choose a rack').selectOption({ label: rackA });
    await page.getByRole('button', { name: 'Add rack' }).click();
    await expect(page.getByRole('heading', { name: /Racks in this row \(1\)/ })).toBeVisible();
    await page.getByLabel('Choose a rack').selectOption({ label: rackB });
    await page.getByRole('button', { name: 'Add rack' }).click();
    await expect(page.getByRole('heading', { name: /Racks in this row \(2\)/ })).toBeVisible();

    // --- Remove rack A from the row (rack itself survives) ---
    const rowA = page.getByRole('listitem').filter({ hasText: rackA });
    await rowA.getByRole('button', { name: 'Remove' }).click();
    await expect(page.getByRole('heading', { name: /Racks in this row \(1\)/ })).toBeVisible();
    await expect(page.getByText('Rack removed', { exact: true })).toBeVisible();

    // --- Delete the row, keeping its remaining rack (dismiss the "also delete" confirm) ---
    await page.getByRole('button', { name: 'Delete row' }).click();
    await expect(page.getByText('Row deleted', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: rowName })).toHaveCount(0);

    // --- Both racks still exist (the row deletion left them alone) ---
    const racksRes = await request.get(`${API_URL}/api/v1/racks`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const names = ((await racksRes.json()) as Array<{ name: string }>).map((r) => r.name);
    expect(names).toContain(rackA);
    expect(names).toContain(rackB);
  } finally {
    await apiDeleteRowsByName(request, token, rowName);
    await apiArchiveRacksByName(request, token, rackA);
    await apiArchiveRacksByName(request, token, rackB);
  }
});
