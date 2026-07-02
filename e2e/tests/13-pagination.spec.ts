import { expect, test } from '@playwright/test';

import {
  apiCreateWork,
  apiDeleteWorksByTitleContains,
  apiLogin,
  apiSetPapersPerPage,
  expectSignedIn,
} from '../helpers';

// Journey 13 — Library pagination (D18). Seed a known number of papers and set the per-user "papers
// per page" preference, then drive prev/next, the page dropdown and go-to-page in the Library, and
// confirm a larger page size collapses the results to a single page. The page-size preference is
// set via the API (the Profile number field is exercised elsewhere); this journey targets the
// Library pager behaviour and that the preference actually changes the page size.
test('Journey 13 — pagination: page size, prev/next, dropdown and go-to-page', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);
  // A distinctive first token makes the metadata search resolve to exactly these six papers.
  const tag = `zqxpage${Date.now()}${Math.floor(Math.random() * 1e6)}`;
  const titles = Array.from({ length: 6 }, (_, i) => `${tag} paginated paper ${i + 1}`);

  try {
    for (const title of titles) await apiCreateWork(request, token, title);

    // --- Page size 2 → 6 / 2 = 3 pages ---
    await apiSetPapersPerPage(request, token, 2);
    await page.goto('/#library');
    await expectSignedIn(page);
    await page.getByLabel('Search', { exact: true }).fill(tag);
    await page.getByRole('button', { name: 'Search' }).click();
    await expect(page.getByText(/6 papers.*page 1 of 3/)).toBeVisible();

    const prev = page.getByRole('button', { name: /Prev/ });
    const next = page.getByRole('button', { name: /Next/ });
    await expect(prev).toBeDisabled(); // first page

    // --- Next → page 2, then the go-to-page number input → last page (Next disabled) ---
    await next.click();
    await expect(page.getByText(/page 2 of 3/)).toBeVisible();
    const goto = page.getByLabel('Go to page number');
    await goto.fill('3');
    await goto.dispatchEvent('change'); // the input navigates on its change handler
    await expect(page.getByText(/page 3 of 3/)).toBeVisible();
    await expect(next).toBeDisabled();

    // --- Page dropdown → jump back to page 1 (Prev disabled) ---
    await page.getByLabel('Go to page', { exact: true }).selectOption('1');
    await expect(page.getByText(/page 1 of 3/)).toBeVisible();
    await expect(prev).toBeDisabled();

    // --- Raise the page size to 10 → the six papers fit on one page (no pager) ---
    await apiSetPapersPerPage(request, token, 10);
    await page.getByLabel('Search', { exact: true }).fill(tag);
    await page.getByRole('button', { name: 'Search' }).click();
    await expect(page.getByText(/6 papers/)).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Library pages' })).toHaveCount(0);
  } finally {
    await apiSetPapersPerPage(request, token, null).catch(() => {});
    await apiDeleteWorksByTitleContains(request, token, tag);
  }
});
