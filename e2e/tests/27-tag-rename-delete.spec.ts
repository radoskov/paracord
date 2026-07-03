import { expect, test } from '@playwright/test';

import { apiDeleteTagsByName, apiLogin, expectSignedIn, uniqueName } from '../helpers';

// Journey 27 — rename a tag, then delete it, all from the Tags page.
test('Journey 27 — rename and delete a tag', async ({ page, request }) => {
  const original = uniqueName('tag-rename');
  const renamed = uniqueName('tag-renamed');
  const token = await apiLogin(request);

  try {
    await page.goto('/#tags');
    await expectSignedIn(page);

    // --- Create the tag ---
    await page.getByLabel('Tag name').fill(original);
    await page.getByRole('button', { name: 'Create tag' }).click();
    await expect(page.getByText('Tag created', { exact: true })).toBeVisible();
    const row = page.getByRole('listitem').filter({ hasText: original });
    await expect(row).toBeVisible();

    // --- Rename it via the inline Edit control ---
    await row.getByRole('button', { name: 'Edit' }).click();
    await page.getByLabel('Edit tag name').fill(renamed);
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText('Tag updated', { exact: true })).toBeVisible();
    await expect(page.getByRole('listitem').filter({ hasText: renamed })).toBeVisible();
    await expect(page.getByRole('listitem').filter({ hasText: original })).toHaveCount(0);

    // --- Delete it (auto-accept the confirm) ---
    page.on('dialog', (d) => d.accept());
    await page
      .getByRole('listitem')
      .filter({ hasText: renamed })
      .getByRole('button', { name: 'Delete' })
      .click();
    await expect(page.getByText('Tag deleted', { exact: true })).toBeVisible();
    await expect(page.getByRole('listitem').filter({ hasText: renamed })).toHaveCount(0);
  } finally {
    await apiDeleteTagsByName(request, token, original);
    await apiDeleteTagsByName(request, token, renamed);
  }
});
