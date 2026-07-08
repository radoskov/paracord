import { expect, test } from '@playwright/test';

import {
  apiCreateWork,
  apiDeleteWorksByTitleContains,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../../helpers';

// Slow E2E journey: not part of the fastest developer loop. Run with `make e2e`
// or directly through Playwright when validating release-level UX flows.
test('slow @slow — search result can be opened, selected, and exported', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);
  const marker = `zqxext${Date.now()}${Math.floor(Math.random() * 1e6)}`;
  const title = `${uniqueName('ext search export')} ${marker}`;

  await apiCreateWork(request, token, title);

  try {
    await page.goto('/#search');
    await expectSignedIn(page);
    await page.getByRole('radiogroup').getByText('Lexical', { exact: true }).click();

    const result = page.getByRole('button', { name: new RegExp(marker) });
    await expect(async () => {
      await page.getByLabel('Search query').fill(marker);
      await page.getByRole('button', { name: 'Search' }).click();
      await expect(result).toBeVisible({ timeout: 3000 });
    }).toPass({ timeout: 30_000 });

    await result.click();
    await page.getByRole('button', { name: 'Open in Library' }).click();
    await expect(page).toHaveURL(/#library$/);
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    await page.getByLabel('Search', { exact: true }).fill(marker);
    await page.getByRole('button', { name: 'Search' }).click();
    // Assert on the results table row (unique via the marker) rather than bare title text: the
    // title also appears in the open detail panel's heading, so a plain getByText is ambiguous.
    const row = page.getByRole('row', { name: new RegExp(marker) });
    await expect(row).toBeVisible();
    await row.getByLabel('Select paper').check();
    await expect(page.getByText('1 selected')).toBeVisible();

    await page.getByLabel('Export selection').selectOption('bibtex');
    await page.getByRole('button', { name: 'Preview' }).click();
    const preview = page.locator('textarea.preview');
    await expect(preview).toBeVisible();
    await expect(preview).toHaveValue(new RegExp(marker));
  } finally {
    await apiDeleteWorksByTitleContains(request, token, marker);
  }
});
