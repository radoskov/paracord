import { expect, test } from '@playwright/test';

import { apiDeleteCustomTheme, apiLogin, apiSetUserTheme, expectSignedIn } from '../helpers';

// A tiny but schema-complete custom theme: the full required token role set (copied from a bundled
// dark theme's shape) plus a `graph.categorical` palette. Presentational graph keys are defaulted
// server-side, so this is the minimal valid upload. `slug` is unique per run (idempotent reruns).
function customThemeYaml(slug: string): string {
  return [
    `id: ${slug}`,
    `name: "E2E Custom ${slug}"`,
    'mode: dark',
    'temperature: cool',
    'tokens:',
    '  surface: {base: "#12131f", raised: "#1c1e2e", overlay: "#262a3d", sunken: "#0d0e17", hover: "#22263a"}',
    '  ink: {strong: "#e6e9f5", normal: "#c7cde0", muted: "#8b90a8", inverse: "#12131f"}',
    '  border: {normal: "#2a2e42", strong: "#3a3f57", focus: "#7aa2f7"}',
    '  accent: {primary: "#7aa2f7", primary-strong: "#6a92ef", secondary: "#c7cde0", link: "#7aa2f7", note: "#c0a7f0", note-bg: "#241f38", note-border: "#3d3560"}',
    '  status:',
    '    success: "#9ece6a"',
    '    success-bg: "#1f2a17"',
    '    success-border: "#3a4d2c"',
    '    warning: "#e0af68"',
    '    warning-bg: "#2c2515"',
    '    warning-border: "#4d3f26"',
    '    danger: "#f7768e"',
    '    danger-bg: "#331b22"',
    '    danger-border: "#552b36"',
    '    info: "#7dcfff"',
    '    info-bg: "#152a33"',
    '    info-border: "#274a55"',
    '  radius: {sm: "6px", md: "8px"}',
    '  font: {family: "Inter, ui-sans-serif, system-ui, sans-serif"}',
    'graph:',
    '  categorical: ["#4a7fd0", "#cf7020", "#1a9a9a", "#e04a68"]',
  ].join('\n');
}

// Journey 32 — an admin uploads a hand-edited YAML theme at runtime; it appears in the picker,
// applies live like a bundled theme (data-theme flips to its slug, tokens change), and can be
// deleted again. The e2e user is an admin, so the admin Themes tab is available.
test('Journey 32 — admin uploads a custom theme, applies it, then deletes it', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);
  const slug = `e2e-custom-${Date.now()}${Math.floor(Math.random() * 1e6)}`;

  // Accept the delete confirmation dialog.
  page.on('dialog', (dialog) => dialog.accept().catch(() => {}));

  try {
    // --- Upload via the admin Themes tab ---
    await page.goto('/#admin');
    await expectSignedIn(page);
    await page.locator('nav.admin-tabs').getByRole('button', { name: 'Themes' }).click();

    await page.getByLabel('Theme YAML').fill(customThemeYaml(slug));
    await page.getByRole('button', { name: 'Save theme' }).click();
    await expect(page.getByText(new RegExp(`Saved theme .*${slug}`))).toBeVisible();
    // It shows in the custom-themes table.
    await expect(page.getByRole('row').filter({ hasText: slug })).toBeVisible();

    // --- It appears in the Profile picker and applies live ---
    await page.goto('/#profile');
    const option = page.getByTestId(`theme-option-${slug}`);
    await expect(option).toBeVisible({ timeout: 15_000 });
    await option.click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', slug);
    await expect(page.getByText('Theme saved.', { exact: true })).toBeVisible();
    // The custom surface token actually applied.
    const surface = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--surface-base').trim(),
    );
    expect(surface).toEqual('#12131f');

    // Reset to a bundled theme before removing the custom one (so we don't sit on a deleted theme).
    await page.getByTestId('theme-option-latte-warm').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'latte-warm');

    // --- Delete it from the admin Themes tab ---
    await page.goto('/#admin');
    await page.locator('nav.admin-tabs').getByRole('button', { name: 'Themes' }).click();
    const row = page.getByRole('row').filter({ hasText: slug });
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Remove' }).click();
    await expect(page.getByRole('row').filter({ hasText: slug })).toHaveCount(0);
  } finally {
    await apiSetUserTheme(request, token, null).catch(() => {});
    await apiDeleteCustomTheme(request, token, slug).catch(() => {});
  }
});
