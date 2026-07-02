import { expect, test } from '@playwright/test';

import { SAMPLE_PDF, apiDeleteWorksByTitle, apiLogin, expectSignedIn, uniqueName } from '../helpers';

// Journey 14 — attach a PDF (reusing journey 8's setup), open the in-app reader, add a note
// annotation on the Notes tab, and assert it appears — then reload and reopen the reader to confirm
// the annotation was persisted server-side.
test('Journey 14 — annotate a paper in the reader and survive a reload', async ({
  page,
  request,
}) => {
  const title = uniqueName('annotate-paper');
  const noteText = `E2E note ${Date.now()}`;
  const token = await apiLogin(request);

  async function attachPdfAndOpenReader(): Promise<void> {
    const filesSection = page
      .locator('details')
      .filter({ has: page.locator('summary', { hasText: 'Files' }) });
    await filesSection.locator('summary', { hasText: 'Files' }).click();
    if ((await filesSection.getByText('sample.pdf', { exact: false }).count()) === 0) {
      await page.getByLabel('Attach PDF').setInputFiles(SAMPLE_PDF);
      await filesSection.locator('button', { hasText: 'Attach PDF' }).click();
      await expect(filesSection.getByText('sample.pdf', { exact: false })).toBeVisible();
    }
    await filesSection.getByRole('button', { name: 'Read', exact: true }).click();
  }

  try {
    // --- Create the paper + attach the fixture PDF, then open the reader ---
    await page.goto('/#library');
    await expectSignedIn(page);
    await page.getByRole('button', { name: '+ New paper' }).click();
    const newDialog = page.getByRole('dialog', { name: 'New paper' });
    await newDialog.getByLabel('Title').fill(title);
    await newDialog.getByRole('button', { name: 'Create paper' }).click();
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    await attachPdfAndOpenReader();
    let reader = page.getByRole('dialog', { name: /sample\.pdf/ });
    await expect(reader.locator('canvas').first()).toBeVisible();

    // --- Add a note on the Notes tab ---
    await reader.getByRole('button', { name: 'Notes' }).click();
    await reader.getByPlaceholder('Page').fill('1');
    await reader.getByPlaceholder('Note').fill(noteText);
    await reader.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(reader.getByText(noteText)).toBeVisible();

    // --- Persistence: reload, reopen the paper + reader, assert the note is still there ---
    await page.reload();
    await expectSignedIn(page);
    await page.getByLabel('Search', { exact: true }).fill(title);
    await page.getByRole('button', { name: 'Search' }).click();
    await page.getByText(title, { exact: true }).click();
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    await attachPdfAndOpenReader();
    reader = page.getByRole('dialog', { name: /sample\.pdf/ });
    await expect(reader.locator('canvas').first()).toBeVisible();
    await reader.getByRole('button', { name: 'Notes' }).click();
    await expect(reader.getByText(noteText)).toBeVisible();
  } finally {
    await apiDeleteWorksByTitle(request, token, title);
  }
});
