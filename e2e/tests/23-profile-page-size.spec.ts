import { expect, test } from '@playwright/test';

import {
  apiCreateWork,
  apiDeleteWorksByTitleContains,
  apiLogin,
  apiSetPapersPerPage,
  expectSignedIn,
} from '../helpers';

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
