import { expect, test } from '@playwright/test';

import {
  API_URL,
  apiDeleteWorksByTitleContains,
  apiLogin,
  expectSignedIn,
} from '../helpers';

// Seed a handful of dated papers so every visualization scope has something to plot. Returns the
// distinctive tag used for cleanup.
async function seedDatedPapers(request: import('@playwright/test').APIRequestContext, token: string) {
  const tag = `zqxviz${Date.now()}${Math.floor(Math.random() * 1e6)}`;
  const years = [2018, 2019, 2020, 2021, 2022];
  for (let i = 0; i < years.length; i += 1) {
    const res = await request.post(`${API_URL}/api/v1/works`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { canonical_title: `${tag} visual paper ${i + 1}`, year: years[i] },
    });
    expect(res.ok(), `seed work failed: ${res.status()}`).toBeTruthy();
  }
  return tag;
}

// Journey 24a — every visualization view builds a payload for the library scope without surfacing an
// error, and the temporal map's X/Y axis dropdowns re-build cleanly.
test('Journey 24 — every visualization view builds without error', async ({ page, request }) => {
  const token = await apiLogin(request);
  const tag = await seedDatedPapers(request, token);

  const errorMsg = page.locator('section.layout > p.msg[role="status"]');
  const build = page.getByTestId('viz-build');

  try {
    await page.goto('/#visualizations');
    await expectSignedIn(page);

    const viewSelect = page.getByTestId('viz-view-select');
    const values: string[] = await viewSelect
      .locator('option')
      .evaluateAll((opts) => opts.map((o) => (o as HTMLOptionElement).value));
    expect(values.length).toBeGreaterThan(0);

    for (const view of values) {
      await viewSelect.selectOption(view);
      await build.click();
      // The build finished when the button re-enables (busy → false).
      await expect(build).toBeEnabled();
      // "Loads without error": no error status line, and a result card (chart or an explicit empty
      // state) is present.
      await expect(errorMsg).toHaveCount(0);
      await expect(
        page.locator('[data-testid="viz-chart"], section.layout .empty').first(),
      ).toBeVisible();
    }

    // --- Temporal map: exercise both axis dropdowns ---
    await viewSelect.selectOption('temporal_map');
    await build.click();
    await expect(build).toBeEnabled();
    await page.getByTestId('viz-x-axis').selectOption('year');
    await page.getByTestId('viz-y-axis').selectOption('citation_count');
    await expect(build).toBeEnabled();
    await expect(errorMsg).toHaveCount(0);
  } finally {
    await apiDeleteWorksByTitleContains(request, token, tag);
  }
});

// Journey 24b — the Citation summary tab renders its analytics blocks for the library scope.
test('Journey 24 — citation summary renders its blocks', async ({ page, request }) => {
  const token = await apiLogin(request);
  const tag = await seedDatedPapers(request, token);

  try {
    await page.goto('/#citation-summary');
    await expectSignedIn(page);
    await page.getByTestId('summary-build').click();

    // The meta line and every analytics block render (each block shows either items or an empty note).
    await expect(page.getByTestId('summary-meta')).toBeVisible();
    for (const id of [
      'summary-most-cited-local',
      'summary-most-cited-external',
      'summary-missing',
      'summary-bridge',
      'summary-isolated',
      'summary-chronological',
    ]) {
      await expect(page.getByTestId(id)).toBeVisible();
    }
    await expect(page.locator('section.layout > p.msg[role="status"]')).toHaveCount(0);
  } finally {
    await apiDeleteWorksByTitleContains(request, token, tag);
  }
});
