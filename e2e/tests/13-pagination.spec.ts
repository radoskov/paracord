import { expect, test } from '@playwright/test';

import {
  apiCreateWork,
  apiDeleteWorksByTitleContains,
  apiLogin,
  apiSetPapersPerPage,
  expectSignedIn,
} from '../helpers';

// Journeys 13 + 23 both mutate the per-user "papers per page" preference on the single shared e2e
// account, so they must never run in parallel workers: one journey's API write/reset yanks the
// pager out from under the other mid-assertion (the "page 3 of 3" vanished / Save disabled with
// "No changes to save" first-attempt flakes). Serial mode keeps them in one worker and retries
// them as a unit.
test.describe.configure({ mode: 'serial' });

// Journey 13 — Library pagination (D18). Seed a known number of papers and set the per-user "papers
// per page" preference, then drive prev/next, the page dropdown and go-to-page in the Library, and
// confirm a larger page size collapses the results to a single page. The page-size preference is
// set via the API (the Profile number field is exercised in Journey 23 below); this journey targets
// the Library pager behaviour and that the preference actually changes the page size.
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
    // Type + Enter like a real user (the input's title says so): a bare fill + synthetic
    // change-event dispatch raced the pager's re-render (`value={page}`), which could reset the
    // input to the current page between the two calls — the value the change handler then read
    // was the old page (observed as a first-attempt flake).
    const goto = page.getByLabel('Go to page number');
    await goto.click();
    await goto.press('ControlOrMeta+a');
    await goto.pressSequentially('3');
    await goto.press('Enter');
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

// Journey 23 — set "Papers per page" from the Profile form (recently fixed so the number field saves
// through the form) and confirm the saved size actually drives the Library pager. Seeds three papers,
// sets the size to 2, and asserts the Library shows two pages; then restores the default.
test('Journey 23 — Profile "Papers per page" saves via the form and resizes the Library', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);
  const tag = `zqxpp${Date.now()}${Math.floor(Math.random() * 1e6)}`;
  const titles = Array.from({ length: 3 }, (_, i) => `${tag} profile paper ${i + 1}`);

  // Type into the type="number" field char-by-char: a bare .fill() doesn't reliably drive the Svelte
  // bind:value on a number input (mirrors the admin-settings journey's helper).
  async function setPerPage(value: string): Promise<void> {
    const input = page.getByLabel('Papers per page');
    await input.click();
    await input.press('ControlOrMeta+a');
    await input.press('Delete');
    if (value) await input.pressSequentially(value);
  }

  try {
    for (const title of titles) await apiCreateWork(request, token, title);

    // --- Save a page size of 2 via the Profile form ---
    await page.goto('/#profile');
    await expectSignedIn(page);
    await setPerPage('2');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByText('Profile saved.', { exact: true })).toBeVisible();

    // --- Persistence: reload and confirm the form still shows 2 ---
    await page.reload();
    await expect(page.getByLabel('Papers per page')).toHaveValue('2', { timeout: 15_000 });

    // --- The Library now paginates the three seeded papers into two pages ---
    await page.goto('/#library');
    await page.getByLabel('Search', { exact: true }).fill(tag);
    await page.getByRole('button', { name: 'Search' }).click();
    await expect(page.getByText(/3 papers.*page 1 of 2/)).toBeVisible();
  } finally {
    await apiSetPapersPerPage(request, token, null).catch(() => {});
    await apiDeleteWorksByTitleContains(request, token, tag);
  }
});
