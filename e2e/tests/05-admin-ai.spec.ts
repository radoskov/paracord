import { expect, test } from '@playwright/test';

import { expectSignedIn } from '../helpers';

// Read-only: assert the AI & Models panel renders provider availability. In PaRacORD this is a
// top-level owner/admin tab (#ai), alongside the Admin tab — not an Admin sub-tab.
test('Journey 5 — AI & Models panel shows provider availability', async ({ page }) => {
  await page.goto('/#ai');
  await expectSignedIn(page);

  await expect(page.getByRole('heading', { name: 'AI & Models' })).toBeVisible();

  // The dependency-free hashed bag-of-words embedder is always an available provider option.
  await expect(page.getByLabel('Embedding provider')).toContainText('hash_bow');

  // At least one capability card reports its engine availability (the built-in baselines always do).
  await expect(page.getByText('Built-in baseline').first()).toBeVisible();

  // Ollama reachability line is part of the availability readout.
  await expect(page.getByText(/Ollama:/)).toBeVisible();
});
