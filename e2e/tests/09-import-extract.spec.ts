import { expect, test } from '@playwright/test';

import {
  apiDeleteWorksByTitleContains,
  apiFindWorkByTitleContains,
  apiListWorkFiles,
  apiLogin,
  expectSignedIn,
  uniqueSamplePdf,
} from '../helpers';

// "Import directly" extracts before storing (batch10 #1): the button's own client-side poll
// waits (up to ~60s) for GROBID via the Redis worker, then auto-commits every successfully
// extracted, non-colliding item. Give the whole journey room but don't hang.
test.setTimeout(90_000);

// Journey 9 — upload a PDF on the Import tab via "Import directly"; GROBID extracts it first,
// then a paper + file are created (already "extracted"). Uploads are content-addressed + deduped,
// so a byte-unique PDF is used to guarantee a *fresh* paper is minted (see uniqueSamplePdf).
test('Journey 9 — import a PDF and (async) extract it via GROBID', async ({ page, request }) => {
  const token = await apiLogin(request);
  // Distinctive alphanumeric token → survives the filename→title transform intact and is findable.
  const tokenId = `zqx${Date.now()}${Math.floor(Math.random() * 1e6)}`;
  const fileName = `E2Eimp-${tokenId}.pdf`; // stem "E2Eimp-<token>" → title "E2Eimp <token>"

  try {
    await page.goto('/#import');
    await expectSignedIn(page);

    // --- Upload via the Import tab's "Upload PDFs" card, "Import directly" mode ---
    await page.getByLabel('PDF files').setInputFiles({
      name: fileName,
      mimeType: 'application/pdf',
      buffer: uniqueSamplePdf(tokenId),
    });
    await page.getByRole('button', { name: 'Import directly' }).click();
    // The button's own poll can take up to ~60s (GROBID via the worker), so give the result
    // banner room to appear.
    const resultBanner = page.getByText(/^Imported \d+ paper/i);
    await expect(resultBanner).toBeVisible({ timeout: 75_000 });

    // --- Lenient: if GROBID was slow/unavailable, "direct" mode skips the item and mints no
    //     paper at all (extraction now happens *before* the Work is created), so skip with a
    //     clear reason rather than flaking. ---
    const resultText = await resultBanner.innerText();
    const created = Number(resultText.match(/^Imported (\d+) paper/i)?.[1] ?? '0');
    test.skip(created === 0, `import created 0 papers (GROBID slow/unavailable?): "${resultText}"`);

    // --- The paper was created; find it by its filename-derived title (poll: creation is quick) ---
    let workId = '';
    await expect
      .poll(
        async () => {
          const w = await apiFindWorkByTitleContains(request, token, tokenId);
          if (w) workId = w.id;
          return workId;
        },
        { timeout: 20_000, message: 'imported paper never appeared' },
      )
      .not.toEqual('');

    // --- A file is attached to it, already "extracted" (extraction ran before the Work existed) ---
    const files = await apiListWorkFiles(request, token, workId);
    expect(files.length).toBeGreaterThan(0);
    const statuses = files.map((f) => f.status).join(', ');
    expect(files.some((f) => f.status === 'extracted'), `file statuses: ${statuses}`).toBeTruthy();
  } finally {
    await apiDeleteWorksByTitleContains(request, token, tokenId);
  }
});
