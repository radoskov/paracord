import { expect, test } from '@playwright/test';

import {
  apiDeleteTagsByName,
  apiDeleteWorksByTitle,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../helpers';

// Journey 22 — tags: create a tag on the Tags tab, then apply it to a paper and remove it again from
// the paper's detail panel (where tag application lives). Applied tags are listed as chips, each with
// its own remove control.
test('Journey 22 — create a tag, apply it to a paper, then remove it', async ({ page, request }) => {
  const token = await apiLogin(request);
  const tagName = uniqueName('tag');
  const paperTitle = uniqueName('tag-paper');

  try {
    // --- Create the tag on the Tags tab ---
    await page.goto('/#tags');
    await expectSignedIn(page);
    await page.getByLabel('Tag name').fill(tagName);
    await page.getByRole('button', { name: 'Create tag' }).click();
    await expect(page.getByText('Tag created', { exact: true })).toBeVisible();
    await expect(page.getByRole('listitem').filter({ hasText: tagName })).toBeVisible();

    // --- Create a paper via the Library UI (its detail panel opens, and it loads the new tag) ---
    await page.goto('/#library');
    await page.getByRole('button', { name: '+ New paper' }).click();
    const dialog = page.getByRole('dialog', { name: 'New paper' });
    await dialog.getByLabel('Title').fill(paperTitle);
    await dialog.getByRole('button', { name: 'Create paper' }).click();
    await expect(page.getByRole('heading', { name: paperTitle })).toBeVisible();

    // --- Apply the tag from the paper's Tags section ---
    const tagsSection = page
      .locator('details')
      .filter({ has: page.locator('summary', { hasText: 'Tags' }) });
    await tagsSection.locator('summary').click();
    await tagsSection.getByLabel('Tag', { exact: true }).selectOption({ label: tagName });
    await tagsSection.getByRole('button', { name: 'Apply' }).click();
    await expect(page.getByText('Tag applied', { exact: true })).toBeVisible();
    await expect(tagsSection.getByLabel('Applied tags').getByText(tagName)).toBeVisible();

    // --- Remove the tag from the paper via its chip's remove control ---
    await tagsSection.getByRole('button', { name: `Remove tag ${tagName}` }).click();
    await expect(page.getByText('Tag removed', { exact: true })).toBeVisible();
    await expect(tagsSection.getByLabel('Applied tags').getByText(tagName)).toHaveCount(0);
  } finally {
    await apiDeleteWorksByTitle(request, token, paperTitle);
    await apiDeleteTagsByName(request, token, tagName);
  }
});
