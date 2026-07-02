import { expect, test } from '@playwright/test';

import { SAMPLE_PDF, apiDeleteWorksByTitle, apiLogin, expectSignedIn, uniqueName } from '../helpers';

// Journey 8 — attach a PDF to a paper via WorkDetail's Files section, then open the in-app reader
// and assert the PDF actually renders (a <canvas> paints and the page count is known).
test('Journey 8 — attach a PDF and open the in-app reader', async ({ page, request }) => {
  const title = uniqueName('attach-paper');
  const token = await apiLogin(request);

  try {
    await page.goto('/#library');
    await expectSignedIn(page);

    // --- Create the paper (auto-selected; its detail panel opens) ---
    await page.getByRole('button', { name: '+ New paper' }).click();
    const newDialog = page.getByRole('dialog', { name: 'New paper' });
    await newDialog.getByLabel('Title').fill(title);
    await newDialog.getByRole('button', { name: 'Create paper' }).click();
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    // --- Attach the fixture PDF via the collapsible Files section ---
    const filesSection = page
      .locator('details')
      .filter({ has: page.locator('summary', { hasText: 'Files' }) });
    await filesSection.locator('summary', { hasText: 'Files' }).click();
    await page.getByLabel('Attach PDF').setInputFiles(SAMPLE_PDF);
    // The file <input> is exposed as a button in Chromium's a11y tree too, so match the real
    // <button> element by tag to disambiguate from the input.
    await filesSection.locator('button', { hasText: 'Attach PDF' }).click();

    // The file appears in the list.
    await expect(filesSection.getByText('sample.pdf', { exact: false })).toBeVisible();

    // --- Open the reader for that file and assert it renders ---
    await filesSection.getByRole('button', { name: 'Read', exact: true }).click();
    const reader = page.getByRole('dialog', { name: /sample\.pdf/ });
    await expect(reader).toBeVisible();
    // pdf.js paints the page onto a <canvas>; the pager shows the (single-page) count.
    await expect(reader.locator('canvas').first()).toBeVisible();
    await expect(reader.getByText('1 / 1')).toBeVisible();
  } finally {
    await apiDeleteWorksByTitle(request, token, title);
  }
});
