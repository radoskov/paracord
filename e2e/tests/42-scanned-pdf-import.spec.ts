import { readFileSync } from 'node:fs';

import { expect, test } from '@playwright/test';

import {
  apiGetAiConfig,
  apiLogin,
  apiSetAiConfig,
  expectSignedIn,
} from '../helpers';

// A trimmed, real scanned article page (no text layer) — resolved relative to this file (ESM).
const SCANNED_PDF = readFileSync(new URL('../fixtures/scanned-sample.pdf', import.meta.url));

// OCR (ocrmypdf) + GROBID on a real scanned page takes a few seconds on the worker; give room.
test.setTimeout(90_000);

// Journey 42 — import a SCANNED PDF (no text layer) via Import → PDF import. GROBID 500s on a raw
// scan, so extraction must run the OCR pre-step first (add a searchable layer) before GROBID. This
// journey uploads a trimmed scanned article and asserts the staging preview EXTRACTS it (real title,
// not "extraction failed") — the regression guard for scanned-PDF imports.
test('Journey 42 — a scanned PDF (no text layer) is OCR-extracted on import', async ({
  page,
  request,
}) => {
  const token = await apiLogin(request);
  // The whole point is the OCR path; make it deterministic regardless of the stack's ambient
  // backend (default is ocrmypdf), then restore the owner's setting.
  const prior = await apiGetAiConfig(request, token);
  await apiSetAiConfig(request, token, { ocr_backend: 'ocrmypdf' });

  try {
    await page.goto('/#import');
    await expectSignedIn(page);

    // --- Upload the scanned fixture and preview (extract-before-store) ---
    await page.getByLabel('PDF files').setInputFiles({
      name: 'scanned-sample.pdf',
      mimeType: 'application/pdf',
      buffer: SCANNED_PDF,
    });
    await page.getByRole('button', { name: 'Preview & choose' }).click();

    // --- The preview must show the item EXTRACTED (OCR ran → GROBID parsed it), not failed ---
    await expect(page.getByText('extracted ✓')).toBeVisible({ timeout: 75_000 });
    await expect(page.getByText(/extraction failed/i)).toHaveCount(0);
    // A real title was parsed from the OCR'd text (the scanned article's actual title), so the row
    // shows more than the bare filename.
    await expect(page.getByText(/ontology/i).first()).toBeVisible();
  } finally {
    await apiSetAiConfig(request, token, { ocr_backend: prior.ocr_backend });
  }
});
