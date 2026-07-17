import { expect, test } from '@playwright/test';

import { apiDeleteWorksByTitle, apiLogin, expectSignedIn, uniqueName } from '../helpers';

// Journey 35 — the Files panel's "From URL…" and "From server path…" modals open, validate, and
// report the backend's refusals. Both negative paths are offline-deterministic: a localhost URL is
// refused by the SSRF hard block before any connection, and /etc/passwd is outside every allowed
// import root. (The happy paths — a real fetch + a whitelisted path — are covered by backend tests
// and the find-on-web machinery; this journey pins the UI wiring.)
test('Journey 35 — attach-from-URL and server-path modals validate and report refusals', async ({
  page,
  request,
}) => {
  const title = uniqueName('remote-attach');
  const token = await apiLogin(request);

  try {
    await page.goto('/#library');
    await expectSignedIn(page);

    // --- Create the paper (auto-selected; its detail panel opens) ---
    await page.getByRole('button', { name: '+ New paper' }).click();
    const newDialog = page.getByRole('dialog', { name: 'New paper' });
    await newDialog.getByLabel('Title').fill(title);
    await newDialog.getByRole('button', { name: 'Create paper' }).click();
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    const filesSection = page
      .locator('details')
      .filter({ has: page.locator('summary', { hasText: 'Files' }) });
    await filesSection.locator('summary', { hasText: 'Files' }).click();

    // --- From URL: an internal-IP URL is hard-blocked (SSRF guard) and the reason surfaces ---
    await filesSection.getByRole('button', { name: 'From URL…' }).click();
    const urlDialog = page.getByRole('dialog', { name: 'Attach a PDF from a URL' });
    await urlDialog.getByLabel('PDF URL').fill('http://127.0.0.1:9/paper.pdf');
    await urlDialog.getByRole('button', { name: 'Proceed' }).click();
    await expect(urlDialog.getByRole('alert')).toBeVisible({ timeout: 15_000 });
    await urlDialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(urlDialog).not.toBeVisible();

    // --- From server path: a path outside the allowed roots is refused with the actionable hint ---
    await filesSection.getByRole('button', { name: 'From server path…' }).click();
    const pathDialog = page.getByRole('dialog', { name: 'Attach a PDF from a server path' });
    await pathDialog.getByLabel('Server file path').fill('/etc/passwd');
    await pathDialog.getByRole('button', { name: 'Proceed' }).click();
    await expect(pathDialog.getByText(/not inside an allowed server folder/i)).toBeVisible();
    await pathDialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(pathDialog).not.toBeVisible();

    // Nothing got attached by either refusal.
    await expect(filesSection.getByText('No files attached', { exact: false })).toBeVisible();
  } finally {
    await apiDeleteWorksByTitle(request, token, title);
  }
});
