import { expect, test } from '@playwright/test';

import {
  apiCreateWork,
  apiDeleteWorksByTitleContains,
  apiLogin,
  expectSignedIn,
} from '../helpers';

// Journey 15 — select a paper in the Library, open the export dialog and exercise two formats
// (BibTeX and a Styled/CSL citation), asserting the previewed content carries the paper's title.
test('Journey 15 — export a selected paper as BibTeX and a styled citation', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);
  // A distinctive token: it is both the search term (one exact match) and the string the export
  // preview must contain (it lives in the paper's title, which every format renders).
  const tag = `zqxexport${Date.now()}${Math.floor(Math.random() * 1e6)}`;
  const title = `${tag} exportable paper`;

  try {
    await apiCreateWork(request, token, title);

    await page.goto('/#library');
    await expectSignedIn(page);
    await page.getByLabel('Search', { exact: true }).fill(tag);
    await page.getByRole('button', { name: 'Search' }).click();

    // Select our paper (scope to its row so stray rows never confuse the checkbox) → the batch bar
    // (with its export dialog) appears.
    await expect(page.getByText(title, { exact: true })).toBeVisible();
    await page
      .getByRole('row', { name: new RegExp(tag) })
      .getByLabel('Select paper')
      .check();
    await expect(page.getByText('1 selected')).toBeVisible();

    const format = page.getByLabel('Export selection');
    const preview = page.locator('textarea.preview');

    // --- BibTeX (the preview is a <textarea>, so assert on its value, not text content) ---
    await format.selectOption('bibtex');
    await page.getByRole('button', { name: 'Preview' }).click();
    await expect(preview).toBeVisible();
    await expect(preview).toHaveValue(/@/);
    await expect(preview).toHaveValue(new RegExp(tag));

    // --- Styled (CSL) ---
    await format.selectOption('styled');
    // The style picker only renders in styled mode; leave the default style.
    await expect(
      page.locator('select[title="Citation style for the formatted output"]'),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Preview' }).click();
    await expect(preview).toHaveValue(new RegExp(tag));
  } finally {
    await apiDeleteWorksByTitleContains(request, token, tag);
  }
});
