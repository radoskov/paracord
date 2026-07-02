import { defineConfig, devices } from '@playwright/test';

import { BASE_URL } from './helpers';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  timeout: 45_000,
  expect: { timeout: 12_000 },
  retries: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  globalSetup: './global-setup.ts',
  use: {
    baseURL: BASE_URL,
    // Signed-in session captured by global-setup; the sign-in spec overrides this.
    storageState: '.auth/state.json',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 12_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
