import { expect, test } from '@playwright/test';

import { expectSignedIn } from '../helpers';

// Journey 43 — Import → Citations. A pasted "Title (YYYY) doi:…" line must split the year + DOI into
// their own fields and keep only the title in the title field (owner-reported: everything landed in
// the title). Uses the GROBID engine (deterministic + local); the parser recovers the year/DOI from
// the raw line regardless of what GROBID returns, so this needs no external network.
test('Journey 43 — Import/Citations parses year + DOI out of a "Title (YYYY) doi:" line', async ({
  page,
}) => {
  await page.goto('/#import');
  await expectSignedIn(page);

  await page
    .getByRole('navigation', { name: 'Import methods' })
    .getByRole('button', { name: 'Citations' })
    .click();

  await page.getByRole('radio', { name: 'GROBID' }).check();
  await page
    .getByPlaceholder(/Attention is all you need/)
    .fill(
      'SceneGraphFusion: Incremental 3D Scene Graph Prediction from RGB-D Sequences (2021) ' +
        'doi:10.1109/cvpr46437.2021.00743',
    );
  await page.getByRole('button', { name: 'Preview', exact: true }).click();

  // The parsed draft splits out the year + DOI; the title no longer carries them. (The Citations
  // tab has a second, empty DraftReview for the BibTeX card, so scope to the first — batch — draft.)
  await expect(page.getByLabel('Year').first()).toHaveValue('2021', { timeout: 30_000 });
  await expect(page.getByLabel('DOI').first()).toHaveValue('10.1109/cvpr46437.2021.00743');
  await expect(page.getByLabel('Title').first()).toHaveValue(
    'SceneGraphFusion: Incremental 3D Scene Graph Prediction from RGB-D Sequences',
  );
});
