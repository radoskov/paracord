import { expect, test } from '@playwright/test';

import { expectSignedIn } from '../helpers';

// Journey 18 — the Jobs tab renders the queue-health semaphore (D7) with a sane status. With Redis
// and a worker up (the dev stack) it should report the queue reachable/healthy; the assertion
// tolerates the brief "reachable but no workers yet" window so it isn't flaky on a cold worker.
test('Journey 18 — Jobs tab shows the queue-health semaphore', async ({ page }) => {
  await page.goto('/#jobs');
  await expectSignedIn(page);
  await expect(page.getByRole('heading', { name: 'Background jobs' })).toBeVisible();

  const semaphore = page.getByTestId('queue-health');
  await expect(semaphore).toBeVisible();
  await expect(semaphore).toHaveText(/Queue (healthy|reachable)/);
});
