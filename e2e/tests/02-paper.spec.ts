import { expect, test } from '@playwright/test';

import { apiDeleteWorksByTitle, apiLogin, expectSignedIn, uniqueName } from '../helpers';

test('Journey 2 — create, edit and persist a paper, then delete it', async ({ page, request }) => {
  const title = uniqueName('paper');
  const token = await apiLogin(request);

  try {
    await page.goto('/#library');
    await expectSignedIn(page);

    // --- Create via the "+ New paper" dialog ---
    await page.getByRole('button', { name: '+ New paper' }).click();
    const dialog = page.getByRole('dialog', { name: 'New paper' });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel('Title').fill(title);
    await dialog.getByRole('button', { name: 'Create paper' }).click();

    // The new paper is auto-selected: its detail panel opens with the title as a heading.
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    // --- Edit a field (year) and save ---
    const year = '2021';
    await page.getByLabel('Year').fill(year);
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByText('Saved', { exact: true })).toBeVisible();

    // --- Persistence: reload, find it via search, reopen, assert the year stuck ---
    await page.reload();
    await expectSignedIn(page);
    await page.getByLabel('Search', { exact: true }).fill(title);
    await page.getByRole('button', { name: 'Search' }).click();

    await page.getByText(title, { exact: true }).click();
    await expect(page.getByLabel('Year')).toHaveValue(year);
  } finally {
    await apiDeleteWorksByTitle(request, token, title);
  }
});
