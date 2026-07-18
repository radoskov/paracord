import { expect, test } from '@playwright/test';

import {
  apiAddWorkToShelf,
  apiCreateShelf,
  apiCreateWork,
  apiDeleteShelvesByName,
  apiDeleteWorksByTitleContains,
  apiLogin,
  expectSignedIn,
  uniqueName,
} from '../helpers';

// Journey 41 — AI "Recommend categorization" (Insights sub-tab). Seed a shelf with one paper via
// the API, then in the UI: open Insights → Recommend categorization, scope to that shelf, run, and
// wait for the per-paper result card. With no generative model configured the run degrades to the
// embedding-cosine fallback (fast + deterministic), so the journey works without an LLM — it
// exercises the whole run → poll → render path and the fallback banner.
test('Journey 41 — run a categorization recommendation over a shelf and see per-paper results', async ({
  page,
  request,
}) => {
  const shelfName = uniqueName('rec-shelf');
  const title = uniqueName('rec-paper');
  const token = await apiLogin(request);
  const shelfId = await apiCreateShelf(request, token, shelfName);
  const workId = await apiCreateWork(request, token, title);
  await apiAddWorkToShelf(request, token, shelfId, workId);

  try {
    await page.goto('/#insights');
    await expectSignedIn(page);

    await page.getByTestId('insights-subtab-recommend').click();
    await page.getByTestId('recommend-scope-type').selectOption('shelf');
    await page.getByTestId('recommend-scope-id').selectOption({ label: shelfName });
    await page.getByTestId('rec-mode').selectOption('categorization');
    await page.getByTestId('rec-run').click();

    // The run is a background job; poll-render lands a per-paper card (generous timeout for the job).
    const paper = page.getByTestId('rec-paper').filter({ hasText: title });
    await expect(paper).toBeVisible({ timeout: 60_000 });
    // The per-paper popups are present (raw scores + raw LLM I/O).
    await expect(paper.getByRole('button', { name: 'Scores' })).toBeVisible();
  } finally {
    await apiDeleteWorksByTitleContains(request, token, title);
    await apiDeleteShelvesByName(request, token, shelfName);
  }
});
