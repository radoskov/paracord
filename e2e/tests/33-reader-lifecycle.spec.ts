import { expect, test } from '@playwright/test';

import { SAMPLE_PDF, apiDeleteWorksByTitle, apiLogin, expectSignedIn, uniqueName } from '../helpers';

// Journey 33 — the reader lifecycle mirroring the reported flow: open via the MAIN "Read" button,
// make a note, zen enter + Esc-exit, close the reader, then RE-OPEN (the "Read works only once"
// bug). Each open must render a fresh canvas. Both the X button and Esc are exercised as close paths.
test('Journey 33 — reader open/note/zen/close/REOPEN via main Read button', async ({
  page,
  request,
}) => {
  const title = uniqueName('reader-life');
  const token = await apiLogin(request);

  try {
    await page.goto('/#library');
    await expectSignedIn(page);

    await page.getByRole('button', { name: '+ New paper' }).click();
    const newDialog = page.getByRole('dialog', { name: 'New paper' });
    await newDialog.getByLabel('Title').fill(title);
    await newDialog.getByRole('button', { name: 'Create paper' }).click();
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    const filesSection = page
      .locator('details')
      .filter({ has: page.locator('summary', { hasText: 'Files' }) });
    await filesSection.locator('summary', { hasText: 'Files' }).click();
    await page.getByLabel('Attach PDF').setInputFiles(SAMPLE_PDF);
    await filesSection.locator('button', { hasText: 'Attach PDF' }).click();
    await expect(filesSection.getByText('sample.pdf', { exact: false })).toBeVisible();

    // The MAIN quick-read Read button below the title (the one the report uses).
    const readBtn = page.locator('.quick-read').getByRole('button', { name: 'Read', exact: true });
    await expect(readBtn).toBeVisible();
    const reader = page.getByRole('dialog', { name: /sample\.pdf/ });

    // Open #1.
    await readBtn.click();
    await expect(reader).toBeVisible();
    await expect(reader.locator('canvas').first()).toBeVisible();

    // Make a note via the Notes tab form.
    await reader.getByRole('button', { name: 'Notes' }).click();
    await reader.locator('textarea[placeholder="Note"]').fill('a test note');
    await reader.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(reader.getByText('Annotation added')).toBeVisible();

    // Back to the PDF pages (the zen/toolbar controls live on the Paper tab).
    await reader.getByRole('button', { name: 'Paper' }).click();

    // Zen enter, then Esc — reader stays open (Esc exits zen, not the reader).
    await reader.getByTestId('reader-zen').click();
    await expect(page.getByTestId('reader-zen')).toHaveText(/Exit zen/);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('reader-zen')).toHaveText(/^Zen$/);

    // Close the reader via Esc (now that zen is off, Esc closes the modal).
    await page.keyboard.press('Escape');
    await expect(reader).toBeHidden();

    // RE-OPEN on the first click (the regression: worked once, then needed a paper switch).
    await readBtn.click();
    await expect(reader).toBeVisible();
    await expect(reader.locator('canvas').first()).toBeVisible();

    // Close via the X button and RE-OPEN a third time.
    await reader.locator('button.close').click();
    await expect(reader).toBeHidden();
    await readBtn.click();
    await expect(reader).toBeVisible();
    await expect(reader.locator('canvas').first()).toBeVisible();
  } finally {
    await apiDeleteWorksByTitle(request, token, title);
  }
});
