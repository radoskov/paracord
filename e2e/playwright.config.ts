import { defineConfig, devices, type ReporterDescription } from '@playwright/test';

import { BASE_URL } from './helpers';

const terminalReporter = (process.env.E2E_REPORTER ?? 'dot') as
  | 'dot'
  | 'list'
  | 'line'
  | 'github';

const reporters: ReporterDescription[] = [
  [terminalReporter],
  ['html', { open: 'never' }],
  ['json', { outputFile: 'test-results/e2e-results.json' }],
];

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  timeout: 45_000,
  expect: {
    timeout: 12_000,
  },
  retries: 1,
  reporter: reporters,
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
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
