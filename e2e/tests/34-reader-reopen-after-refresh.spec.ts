import { expect, test } from '@playwright/test';

import { SAMPLE_PDF, apiDeleteWorksByTitle, apiLogin, expectSignedIn, uniqueName } from '../helpers';

// Journey 34 — the "Read works once, then the button does nothing until I switch papers" regression.
// It only reproduces when a background job-poll refresh (refreshOpenWork -> loadDetail) reassigns the
// reader's props WHILE it is open: the reader's onDestroy then threw during Svelte 5's effect
// teardown, corrupting the effect tree so the {#key readerUrl} block could never re-mount. A job-less
// e2e paper (Journey 33) never triggers the poll, so it never caught this. Here we enqueue a
// background job, keep the reader open across two 4s poll ticks, then close and RE-OPEN — asserting
// both that the reader comes back AND that no uncaught page error escaped teardown.
test('Journey 34 — reader re-opens after a background refresh fired mid-view', async ({
  page,
  request,
}) => {
  const title = uniqueName('reader-refresh');
  const token = await apiLogin(request);
  const pageErrors: string[] = [];
  page.on('pageerror', (e) => pageErrors.push(`${e.message}\n${e.stack ?? ''}`));

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

    // Enqueue a background job so watchWorkJobs polls with an in-flight job id — this makes the poll
    // fire refreshOpenWork ~4-8s later (mid-view), exactly like a user's real extract/summary jobs.
    const extractBtn = page.getByRole('button', { name: 'Extract', exact: true });
    await expect(extractBtn).toBeEnabled();
    await extractBtn.click();

    const readBtn = page.locator('.quick-read').getByRole('button', { name: 'Read', exact: true });
    const reader = page.getByRole('dialog', { name: /sample\.pdf/ });

    // Open, then hold across at least two 4s poll ticks so a refresh reassigns props under the reader.
    await readBtn.click();
    await expect(reader).toBeVisible();
    await expect(reader.locator('canvas').first()).toBeVisible();
    await page.waitForTimeout(10_000);

    // Close, then RE-OPEN — the manual failure point.
    await page.keyboard.press('Escape');
    await expect(reader).toBeHidden();

    await expect(readBtn).toBeEnabled();
    await readBtn.click();
    await expect(reader).toBeVisible({ timeout: 6000 });
    await expect(reader.locator('canvas').first()).toBeVisible();

    expect(pageErrors, `uncaught page errors:\n${pageErrors.join('\n---\n')}`).toEqual([]);
  } finally {
    await apiDeleteWorksByTitle(request, token, title);
  }
});
