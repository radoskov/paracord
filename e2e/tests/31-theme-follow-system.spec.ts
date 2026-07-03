import { expect, test } from '@playwright/test';

import { apiLogin, apiSetUserTheme, expectSignedIn } from '../helpers';

// Journey 31 — "Follow system appearance": with the toggle on, the app picks the light/dark member
// of the current temperature pair from the OS `prefers-color-scheme` and re-picks when the OS flips.
// Playwright drives the media feature via `emulateMedia`, which also fires the matchMedia change
// listener the store subscribes to. The boot theme is `latte-warm` (warm), so the pair is
// latte-warm ↔ mocha-warm.
test('Journey 31 — follow system appearance resolves the light/dark member of the pair', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);

  try {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto('/#profile');
    await expectSignedIn(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'latte-warm');

    // --- Enable follow-system while the OS reports dark → the warm-dark member ---
    const follow = page.getByTestId('follow-system');
    await expect(follow).not.toBeChecked();
    await follow.check();
    await expect(follow).toBeChecked();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'mocha-warm');

    // --- OS flips to light → follows to the warm-light member (change listener) ---
    await page.emulateMedia({ colorScheme: 'light' });
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'latte-warm');

    // --- OS flips back to dark → follows again ---
    await page.emulateMedia({ colorScheme: 'dark' });
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'mocha-warm');
  } finally {
    await page.emulateMedia({ colorScheme: null }).catch(() => {});
    await apiSetUserTheme(request, token, null).catch(() => {});
  }
});
