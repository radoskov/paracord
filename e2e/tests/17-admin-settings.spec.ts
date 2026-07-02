import { expect, test } from '@playwright/test';

import { expectSignedIn } from '../helpers';

// Journey 17 — set a global app-config value (the D18 "Global max papers per page" clamp) in the
// Admin → Settings tab and assert it persists across a reload. Restores the original value so the
// run is idempotent and doesn't disturb other journeys' page sizes.
test('Journey 17 — admin settings: change a global value and confirm it persists', async ({
  page,
}) => {
  await page.goto('/#admin');
  await expectSignedIn(page);
  await page.getByRole('button', { name: 'Settings' }).click();

  const field = page.getByLabel('Global max papers per page');
  await expect(field).toBeVisible();
  // The app-config loads asynchronously and seeds this field; wait for that before typing, else the
  // late load overwrites our input and the old value gets saved.
  await expect(field).not.toHaveValue('', { timeout: 15_000 });
  const original = (await field.inputValue()) || '500';
  // A value well above any per-page size the suite uses, distinct from the current one.
  const target = original === '250' ? '300' : '250';

  const librarySection = page
    .locator('section')
    .filter({ hasText: 'Global max papers per page' });

  // Type into the number field char-by-char: a bare .fill() doesn't reliably drive the Svelte
  // bind:value on a type="number" input (the model can keep the old value, saving it instead).
  async function setField(value: string): Promise<void> {
    const input = page.getByLabel('Global max papers per page');
    await input.click();
    await input.press('ControlOrMeta+a');
    await input.press('Delete');
    await input.pressSequentially(value);
  }

  try {
    await setField(target);
    await librarySection.getByRole('button', { name: 'Save' }).click();
    await expect(librarySection.getByText('Saved.')).toBeVisible();

    // --- Persistence: reload, reopen Admin → Settings, assert the stored value ---
    await page.reload();
    await expectSignedIn(page);
    await page.getByRole('button', { name: 'Settings' }).click();
    // The app-config loads asynchronously after the page mounts; wait for the stored value.
    await expect(page.getByLabel('Global max papers per page')).toHaveValue(target, {
      timeout: 20_000,
    });
  } finally {
    // Restore the original ceiling.
    await setField(original).catch(() => {});
    await librarySection.getByRole('button', { name: 'Save' }).click().catch(() => {});
  }
});
