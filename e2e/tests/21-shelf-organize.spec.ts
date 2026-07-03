import { expect, test } from '@playwright/test';

import {
  apiCreateShelf,
  apiCreateWork,
  apiDeleteShelvesByName,
  apiDeleteWorksByTitle,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../helpers';

// Journey 21 — shelf organisation: put one paper on two shelves, remove it from one, and confirm it
// stays on the other (so it is never "loose"). Exercises multi-shelf membership + reassignment from
// the Shelves UI, then confirms membership from the paper's own "Organization" panel in the Library.
test('Journey 21 — a paper on two shelves, remove from one, stays on the other', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);
  const shelfA = uniqueName('org-shelf-a');
  const shelfB = uniqueName('org-shelf-b');
  const paperTitle = uniqueName('org-paper');

  const workId = await apiCreateWork(request, token, paperTitle);
  await apiCreateShelf(request, token, shelfA);
  await apiCreateShelf(request, token, shelfB);

  try {
    await page.goto('/#shelves');
    await expectSignedIn(page);

    // Helper: select a shelf on the left and file the paper onto it.
    async function fileOnto(shelfName: string): Promise<void> {
      await page.getByRole('button', { name: shelfName }).click();
      await expect(page.getByRole('heading', { name: shelfName })).toBeVisible();
      await page.getByLabel('Filter papers', { exact: true }).fill(paperTitle);
      await page.getByLabel('Choose a paper').selectOption({ label: paperTitle });
      await page.getByRole('button', { name: 'Add paper' }).click();
      await expect(page.getByRole('heading', { name: /Papers in this shelf \(1\)/ })).toBeVisible();
    }

    // --- File the same paper onto both shelves (multi-shelf membership) ---
    await fileOnto(shelfA);
    await fileOnto(shelfB);

    // --- Remove the paper from shelf A only ---
    await page.getByRole('button', { name: shelfA }).click();
    await expect(page.getByRole('heading', { name: shelfA })).toBeVisible();
    await page.getByRole('listitem').filter({ hasText: paperTitle }).getByRole('button', {
      name: 'Remove',
    }).click();
    await expect(page.getByRole('heading', { name: /Papers in this shelf \(0\)/ })).toBeVisible();

    // --- Shelf B still holds it ---
    await page.getByRole('button', { name: shelfB }).click();
    await expect(page.getByRole('heading', { name: /Papers in this shelf \(1\)/ })).toBeVisible();
    await expect(page.getByRole('listitem').filter({ hasText: paperTitle })).toBeVisible();

    // --- The paper's own "Organization" panel confirms it is on shelf B and not loose ---
    await page.goto('/#library');
    await page.getByLabel('Search', { exact: true }).fill(paperTitle);
    await page.getByRole('button', { name: 'Search' }).click();
    await page.getByRole('row').filter({ hasText: paperTitle }).click();
    await expect(page.getByRole('heading', { name: paperTitle })).toBeVisible();
    const orgSection = page
      .locator('details')
      .filter({ has: page.locator('summary', { hasText: 'Organization' }) });
    await orgSection.locator('summary').click();
    const orgList = orgSection.locator('ul.locations');
    await expect(orgList.getByText(shelfB, { exact: false })).toBeVisible();
    await expect(orgList.getByText(shelfA, { exact: false })).toHaveCount(0);
  } finally {
    await apiDeleteShelvesByName(request, token, shelfA);
    await apiDeleteShelvesByName(request, token, shelfB);
    await apiDeleteWorksByTitle(request, token, paperTitle);
    void workId;
  }
});
