import { expect, test } from '@playwright/test';

import { SAMPLE_PDF, apiDeleteWorksByTitle, apiLogin, expectSignedIn, uniqueName } from '../helpers';

// Journey 33 — the reader lifecycle: open, close, RE-OPEN (the "Read works only once" bug), plus
// zen enter/exit-via-Esc and the reading-mode switch. Each open must render a fresh canvas.
test('Journey 33 — reader open/close/reopen, zen, reading modes', async ({ page, request }) => {
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

    const readBtn = filesSection.getByRole('button', { name: 'Read', exact: true });
    const reader = page.getByRole('dialog', { name: /sample\.pdf/ });
    const closeBtn = () => reader.locator('button.close');

    // Open #1.
    await readBtn.click();
    await expect(reader).toBeVisible();
    await expect(reader.locator('canvas').first()).toBeVisible();

    // Close.
    await closeBtn().click();
    await expect(reader).toBeHidden();

    // RE-OPEN on the very first click (the regression: needed two clicks / broke until paper switch).
    await readBtn.click();
    await expect(reader).toBeVisible();
    await expect(reader.locator('canvas').first()).toBeVisible();

    // Reading modes: dark → dim → original (buttons must stay usable).
    await reader.getByTestId('reading-mode-dark').click();
    await reader.getByTestId('reading-mode-dim').click();
    await reader.getByTestId('reading-mode-original').click();

    // Zen: enter, then exit via Esc — the reader must STAY open (Esc exits zen, not the reader).
    const zenBtn = reader.getByTestId('reader-zen');
    await zenBtn.click();
    await expect(page.getByTestId('reader-zen')).toHaveText(/Exit zen/);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('reader-zen')).toHaveText(/^Zen$/);
    await expect(reader).toBeVisible();

    // Close and RE-OPEN once more — the third open must also work.
    await closeBtn().click();
    await expect(reader).toBeHidden();
    await readBtn.click();
    await expect(reader).toBeVisible();
    await expect(reader.locator('canvas').first()).toBeVisible();
  } finally {
    await apiDeleteWorksByTitle(request, token, title);
  }
});
