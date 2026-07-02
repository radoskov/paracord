import { expect, test } from '@playwright/test';

import {
  apiDeleteShelvesByName,
  apiDeleteWorksByTitle,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../helpers';

// Journey 7 — hard-delete a shelf and assert the paper falls back to the default "Inbox" shelf.
// Semantics (default_shelf.py): a new paper lands on Inbox; filing it onto a real shelf drops it
// from Inbox; deleting that shelf leaves the paper loose, so it falls back onto Inbox again.
test('Journey 7 — delete a shelf; a paper only on it falls back to the Inbox', async ({
  page,
  request,
}) => {
  const shelfName = uniqueName('del-shelf');
  const paperTitle = uniqueName('del-paper');
  const token = await apiLogin(request);

  // Auto-accept the "Delete shelf" confirm() prompt.
  page.on('dialog', (d) => void d.accept());

  try {
    // --- Create the paper via the Library UI (it lands on the Inbox by default) ---
    await page.goto('/#library');
    await expectSignedIn(page);
    await page.getByRole('button', { name: '+ New paper' }).click();
    const dialog = page.getByRole('dialog', { name: 'New paper' });
    await dialog.getByLabel('Title').fill(paperTitle);
    await dialog.getByRole('button', { name: 'Create paper' }).click();
    await expect(page.getByRole('heading', { name: paperTitle })).toBeVisible();

    // --- Create a shelf and file the paper onto it (this removes it from the Inbox) ---
    await page.goto('/#shelves');
    await page.getByLabel('New shelf name').fill(shelfName);
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(page.getByRole('heading', { name: shelfName })).toBeVisible();

    await page.getByLabel('Filter papers', { exact: true }).fill(paperTitle);
    await page.getByLabel('Choose a paper').selectOption({ label: paperTitle });
    await page.getByRole('button', { name: 'Add paper' }).click();
    await expect(page.getByRole('heading', { name: /Papers in this shelf \(1\)/ })).toBeVisible();

    // --- Delete the shelf (accepts the confirm dialog) ---
    await page.getByRole('button', { name: 'Delete shelf' }).click();
    await expect(page.getByText('Shelf deleted', { exact: true })).toBeVisible();

    // The shelf is gone from the left-hand list…
    await expect(page.getByRole('button', { name: shelfName })).toHaveCount(0);

    // …and the paper now sits on the default Inbox shelf again.
    await page.getByRole('button', { name: /^Inbox/ }).click();
    await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible();
    // Scope to the shelf's member list — other tabs (Library table/detail) stay mounted-but-hidden
    // and would otherwise also match the title.
    await expect(page.getByRole('listitem').filter({ hasText: paperTitle })).toBeVisible();
  } finally {
    await apiDeleteShelvesByName(request, token, shelfName);
    await apiDeleteWorksByTitle(request, token, paperTitle);
  }
});
