import { expect, test } from '@playwright/test';

import {
  apiCreateTag,
  apiDeleteTagsByName,
  apiDeleteWorksByTitle,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../helpers';

// Journey 28 — a paper lists its applied tags, and loses a tag when that tag is deleted.
test('Journey 28 — a paper shows its applied tags and loses a deleted one', async ({
  page,
  request,
}) => {
  const tagName = uniqueName('applied-tag');
  const paperTitle = uniqueName('applied-tag-paper');
  const token = await apiLogin(request);
  await apiCreateTag(request, token, tagName);

  const tagsSection = page
    .locator('details')
    .filter({ has: page.locator('summary', { hasText: 'Tags' }) });

  try {
    // --- Create a paper via the Library UI (its detail panel opens) ---
    await page.goto('/#library');
    await expectSignedIn(page);
    await page.getByRole('button', { name: '+ New paper' }).click();
    const dialog = page.getByRole('dialog', { name: 'New paper' });
    await dialog.getByLabel('Title').fill(paperTitle);
    await dialog.getByRole('button', { name: 'Create paper' }).click();
    await expect(page.getByRole('heading', { name: paperTitle })).toBeVisible();

    // --- Apply the tag from the paper's Tags section ---
    await tagsSection.locator('summary').click();
    await expect(tagsSection.getByText('No tags applied yet.')).toBeVisible();
    await tagsSection.getByLabel('Tag', { exact: true }).selectOption({ label: tagName });
    await tagsSection.getByRole('button', { name: 'Apply' }).click();
    await expect(page.getByText('Tag applied', { exact: true })).toBeVisible();

    // --- The applied tag is now listed as a chip on the paper ---
    await expect(tagsSection.getByLabel('Applied tags').getByText(tagName)).toBeVisible();

    // --- Delete the tag globally, then reopen the paper: it must lose the tag ---
    await apiDeleteTagsByName(request, token, tagName);
    await page.getByTitle('Close detail panel').click();
    await page.getByRole('row').filter({ hasText: paperTitle }).click();
    await expect(page.getByRole('heading', { name: paperTitle })).toBeVisible();
    await tagsSection.locator('summary').click();
    await expect(tagsSection.getByText('No tags applied yet.')).toBeVisible();
    await expect(tagsSection.getByLabel('Applied tags').getByText(tagName)).toHaveCount(0);
  } finally {
    await apiDeleteTagsByName(request, token, tagName);
    await apiDeleteWorksByTitle(request, token, paperTitle);
  }
});
