import { expect, test } from '@playwright/test';

import {
  API_URL,
  apiDeleteCustomTheme,
  apiDeleteWorksByTitleContains,
  apiLogin,
  apiSetUserTheme,
  expectSignedIn,
} from '../helpers';

// Journeys 29–32 all mutate the single shared e2e account's server-persisted theme (and assert the
// `latte-warm` boot default, which only holds while no sibling has the theme set), so they must
// never run in parallel workers: one journey's save or `apiSetUserTheme(null)` cleanup lands mid-
// assertion in another (the "/auth/me theme === mocha-cool got null" first-attempt flake). Serial
// mode keeps the whole family in one worker and retries it as a unit.
test.describe.configure({ mode: 'serial' });

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
