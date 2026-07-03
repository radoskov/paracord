import { expect, test } from '@playwright/test';

import { API_URL, apiLogin, apiSetUserTheme, expectSignedIn } from '../helpers';

// Read a CSS custom property off <html> (the token vars `applyTheme` injects, e.g. `--surface-base`).
async function readVar(page: import('@playwright/test').Page, name: string): Promise<string> {
  return page.evaluate(
    (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(),
    name,
  );
}

// Journey 29 — pick a different theme in Profile → Appearance and confirm it actually applies
// (data-theme flips + a surface token changes), persists across a reload (localStorage cache +
// server profile), then switch back. The stack boots to `latte-warm`; we switch to a dark theme.
test('Journey 29 — switching theme applies live and persists across reload', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);

  try {
    await page.goto('/#profile');
    await expectSignedIn(page);
    await expect(page.getByRole('heading', { name: 'Appearance' })).toBeVisible();

    // Baseline: the boot default is the warm light theme.
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'latte-warm');
    const beforeSurface = await readVar(page, '--surface-base');

    // --- Pick a dark theme; the whole app restyles immediately ---
    await page.getByTestId('theme-option-mocha-cool').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'mocha-cool');
    await expect(page.getByText('Theme saved.', { exact: true })).toBeVisible();

    // A real surface token changed (not just the attribute).
    const afterSurface = await readVar(page, '--surface-base');
    expect(afterSurface).not.toEqual('');
    expect(afterSurface).not.toEqual(beforeSurface);

    // Server persisted it (mirrors papers_per_page).
    const meRes = await request.get(`${API_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(meRes.ok()).toBeTruthy();
    expect((await meRes.json()).theme).toEqual('mocha-cool');

    // localStorage cache written for a no-flash boot.
    expect(await page.evaluate(() => localStorage.getItem('paracord-theme'))).toEqual('mocha-cool');

    // --- Persistence: reload and confirm the theme survives (cache + server) ---
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'mocha-cool', {
      timeout: 15_000,
    });

    // --- Switch back to the default ---
    await page.goto('/#profile');
    await page.getByTestId('theme-option-latte-warm').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'latte-warm');
    await expect(page.getByText('Theme saved.', { exact: true })).toBeVisible();
  } finally {
    // Reset the persisted preference so other journeys boot from a known default.
    await apiSetUserTheme(request, token, null).catch(() => {});
  }
});
