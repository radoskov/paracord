import { expect, test } from '@playwright/test';

import { apiDeleteWorksByTitleContains, apiLogin, expectSignedIn } from '../helpers';

// Journey 19 — import a paper by identifier (arXiv) from the Import tab. This hits an external
// metadata provider (arXiv), so it is gated behind E2E_ONLINE to keep CI (offline) deterministic:
// set E2E_ONLINE=1 to run it. A well-known, stable arXiv id is used; the import is idempotent.
const ONLINE = !!process.env.E2E_ONLINE;
const ARXIV_ID = '1706.03762'; // "Attention Is All You Need"
const KNOWN_TITLE_NEEDLE = 'Attention Is All You Need';

test.setTimeout(60_000);

test('Journey 19 — import a paper by arXiv identifier', async ({ page, request }) => {
  test.skip(!ONLINE, 'external network import — set E2E_ONLINE=1 to run');
  const token = await apiLogin(request);

  try {
    await page.goto('/#import');
    await expectSignedIn(page);

    // The Import page groups its ingest paths behind method tabs (UX batch); the identifier
    // form only renders on the "Identifier" tab.
    await page
      .getByRole('navigation', { name: 'Import methods' })
      .getByRole('button', { name: 'Identifier' })
      .click();

    await page.getByLabel('arXiv id or DOI').fill(ARXIV_ID);
    await page
      .locator('form')
      .filter({ has: page.getByLabel('arXiv id or DOI') })
      .getByRole('button', { name: 'Import directly' })
      .click();

    // Either freshly created (with enrichment) or already present and re-enriched.
    await expect(
      page.getByText(/Imported as arXiv|Already in the library/),
    ).toBeVisible({ timeout: 40_000 });
  } finally {
    await apiDeleteWorksByTitleContains(request, token, KNOWN_TITLE_NEEDLE);
  }
});
