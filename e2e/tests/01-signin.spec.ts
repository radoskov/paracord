import { expect, test } from '@playwright/test';

import { PASSWORD, USERNAME, expectSignedIn } from '../helpers';

// Start this spec logged OUT (ignore the shared authenticated storage state) so we exercise the
// real login form.
test.use({ storageState: { cookies: [], origins: [] } });

test('Journey 1 — sign in with the seeded admin credentials', async ({ page }) => {
  await page.goto('/');

  // Logged-out view: the sign-in form.
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.locator('input[autocomplete="username"]').fill(USERNAME);
  await page.locator('input[autocomplete="current-password"]').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();

  // Signed-in view: the tab nav + Library section appear.
  await expectSignedIn(page);
  await expect(page.getByRole('link', { name: 'Search' })).toBeVisible();
});
