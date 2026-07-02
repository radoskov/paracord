import { chromium, expect, type FullConfig } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { BASE_URL, PASSWORD, USERNAME } from './helpers';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Sign in once through the real login UI and persist the resulting session (the JWT lives in
 * localStorage under `paracord_token`) to `.auth/state.json`. Every spec loads that storage state so
 * it starts already authenticated — the dedicated sign-in spec resets it to test the login flow.
 */
export default async function globalSetup(_config: FullConfig): Promise<void> {
  const authDir = path.join(__dirname, '.auth');
  fs.mkdirSync(authDir, { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage();
  try {
    await page.goto(BASE_URL);
    await page.locator('input[autocomplete="username"]').fill(USERNAME);
    await page.locator('input[autocomplete="current-password"]').fill(PASSWORD);
    await page.getByRole('button', { name: 'Sign in' }).click();
    // The tab nav only renders once authenticated.
    await expect(page.getByRole('link', { name: 'Library' })).toBeVisible({ timeout: 20_000 });
    await page.context().storageState({ path: path.join(authDir, 'state.json') });
  } finally {
    await browser.close();
  }
}
