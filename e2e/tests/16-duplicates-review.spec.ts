import { expect, test } from '@playwright/test';

import {
  apiCreateWork,
  apiDeleteWorksByTitleContains,
  apiLogin,
  expectSignedIn,
} from '../helpers';

// A full-library scan runs inline over every visible paper; give it room but don't hang.
test.setTimeout(90_000);

// Journey 16 — create two near-duplicate papers, run the duplicate scan from the UI, see the
// candidate, then resolve it with "Keep separate" (mark-not-duplicate) and confirm it leaves the
// open list. The two titles share a distinctive first token (the fuzzy-title blocking key) and
// differ by a single trailing character, so their similarity clears the detector's threshold.
test('Journey 16 — scan for a duplicate candidate and mark it not-a-duplicate', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);
  const tag = `zqxdup${Date.now()}${Math.floor(Math.random() * 1e6)}`;
  const base = `${tag} duplicate detection neural network survey part`;

  try {
    await apiCreateWork(request, token, `${base} a`);
    await apiCreateWork(request, token, `${base} b`);

    await page.goto('/#duplicates');
    await expectSignedIn(page);
    await expect(page.getByRole('heading', { name: 'Duplicate & version review' })).toBeVisible();

    await page.getByRole('button', { name: 'Scan now' }).click();

    // The candidate card labels both papers, so it carries our distinctive tag.
    const candidate = page.locator('article').filter({ hasText: tag });
    await expect(candidate).toBeVisible({ timeout: 60_000 });

    // Resolve as "not a duplicate" — non-destructive; it drops out of the open list.
    await candidate.getByRole('button', { name: 'Keep separate' }).click();
    await expect(page.getByText(/Applied keep separate/)).toBeVisible();
    await expect(page.locator('article').filter({ hasText: tag })).toHaveCount(0);
  } finally {
    await apiDeleteWorksByTitleContains(request, token, tag);
  }
});
