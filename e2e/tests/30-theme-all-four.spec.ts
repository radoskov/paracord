import { expect, test } from '@playwright/test';

import {
  API_URL,
  apiDeleteWorksByTitleContains,
  apiLogin,
  apiSetUserTheme,
  expectSignedIn,
} from '../helpers';

// The four bundled themes (2 light + 2 dark, each warm/cool).
const THEMES = ['latte-warm', 'latte-cool', 'mocha-warm', 'mocha-cool'] as const;

// Journey 30 — every built-in theme applies from the picker without breaking the app, and the
// Visualizations page still renders a chart under a non-default (dark) theme (live restyle didn't
// break ECharts).
test('Journey 30 — all four built-in themes apply and charts still render', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);
  const tag = `zqxthm${Date.now()}${Math.floor(Math.random() * 1e6)}`;

  try {
    // Seed a few dated papers so the Visualizations chart has something to plot.
    for (let i = 0; i < 3; i += 1) {
      const res = await request.post(`${API_URL}/api/v1/works`, {
        headers: { Authorization: `Bearer ${token}` },
        data: { canonical_title: `${tag} themed paper ${i + 1}`, year: 2020 + i },
      });
      expect(res.ok(), `seed work failed: ${res.status()}`).toBeTruthy();
    }

    await page.goto('/#profile');
    await expectSignedIn(page);

    // --- Each theme applies; a key element stays visible (no render error) ---
    for (const id of THEMES) {
      await page.getByTestId(`theme-option-${id}`).click();
      await expect(page.locator('html')).toHaveAttribute('data-theme', id);
      await expect(page.getByText('Theme saved.', { exact: true })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Appearance' })).toBeVisible();
    }

    // --- Under the last-applied (dark) theme, the Visualizations page still builds a chart ---
    expect(THEMES[THEMES.length - 1]).toEqual('mocha-cool');
    await page.goto('/#visualizations');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'mocha-cool');
    await page.getByTestId('viz-build').click();
    await expect(page.getByTestId('viz-build')).toBeEnabled();
    await expect(page.locator('section.layout > p.msg[role="status"]')).toHaveCount(0);
    await expect(
      page.locator('[data-testid="viz-chart"], section.layout .empty').first(),
    ).toBeVisible();
  } finally {
    await apiSetUserTheme(request, token, null).catch(() => {});
    await apiDeleteWorksByTitleContains(request, token, tag);
  }
});
