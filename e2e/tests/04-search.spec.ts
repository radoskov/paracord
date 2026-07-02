import { expect, test } from '@playwright/test';

import {
  apiCreateWork,
  apiDeleteWorksByTitle,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../helpers';

test('Journey 4 — lexical search finds a paper, shows relevance, and opens it', async ({
  page,
  request,
}) => {
  // A distinctive nonsense token guarantees this is the only lexical match.
  const token = await apiLogin(request);
  const distinctive = `zqxwombat${Date.now()}`;
  const title = `${uniqueName('search')} ${distinctive}`;
  await apiCreateWork(request, token, title);

  try {
    await page.goto('/#search');
    await expectSignedIn(page);

    // Lexical mode needs no AI. The radios are visually hidden, so click the label text.
    await page.getByRole('radiogroup').getByText('Lexical', { exact: true }).click();
    await page.getByLabel('Search query').fill(distinctive);
    await page.getByRole('button', { name: 'Search' }).click();

    // The result appears with a relevance percentage.
    const result = page.getByRole('button', { name: new RegExp(distinctive) });
    await expect(result).toBeVisible();
    await expect(result).toContainText('%');

    // Click the result → reveal actions → open it in the Library, which shows its detail.
    await result.click();
    await page.getByRole('button', { name: 'Open in Library' }).click();
    await expect(page).toHaveURL(/#library$/);
    await expect(page.getByRole('heading', { name: title })).toBeVisible();
  } finally {
    await apiDeleteWorksByTitle(request, token, title);
  }
});
