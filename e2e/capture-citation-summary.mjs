import { chromium } from '@playwright/test';

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173';
const USERNAME = process.env.SHOT_USER ?? 'admin';
const PASSWORD = process.env.SHOT_PASS ?? 'paperracks';
const OUT = process.env.SHOT_OUT ?? '/home/zednik/paracord-theme-shots/citation_summary_enriched.png';

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
});
const page = await context.newPage();
try {
  await page.goto(BASE_URL);
  await page.locator('input[autocomplete="username"]').fill(USERNAME);
  await page.locator('input[autocomplete="current-password"]').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Library' }).waitFor({ timeout: 20000 });

  await page.getByRole('link', { name: 'Citation summary' }).click();
  await page.getByTestId('summary-build').waitFor({ timeout: 20000 });
  for (let attempt = 0; attempt < 4; attempt++) {
    await page.getByTestId('summary-build').click();
    try {
      await page.getByTestId('summary-coverage').waitFor({ timeout: 15000 });
      break;
    } catch {
      await page.waitForTimeout(1500);
    }
  }
  await page.getByTestId('summary-coverage').waitFor({ timeout: 15000 });

  // Open one external preview so the panel is visible in the shot (C1).
  const previewToggle = page.getByTestId('summary-preview-toggle').first();
  if (await previewToggle.count()) {
    await previewToggle.click();
    await page.getByTestId('summary-preview').first().waitFor({ timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1500);
  }

  await page.screenshot({ path: OUT, fullPage: true });
  console.log('wrote', OUT);
} finally {
  await browser.close();
}
