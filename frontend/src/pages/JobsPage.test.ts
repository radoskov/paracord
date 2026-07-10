import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { JobRecord, QueueStatus } from '../api/client';
import { currentUser } from '../lib/session';
import JobsPage from './JobsPage.svelte';

function job(overrides: Partial<JobRecord>): JobRecord {
  return {
    id: 'j',
    task: 'extract',
    status: 'finished',
    enqueued_at: null,
    ended_at: null,
    error: null,
    ...overrides,
  };
}

function makeStatus(jobs: JobRecord[]): QueueStatus {
  return {
    available: true,
    workers: 1,
    counts: { queued: 0, started: 0, finished: jobs.length, failed: 0, scheduled: 0, deferred: 0 },
    jobs,
  };
}

describe('JobsPage queue-health semaphore (D7)', () => {
  it('shows GREEN when Redis is reachable and workers are running', async () => {
    const status: QueueStatus = {
      ...makeStatus([]),
      redis_reachable: true,
      worker_count: 2,
      queued: 3,
    };
    const client = { getJobs: vi.fn().mockResolvedValue(status) };
    render(JobsPage, { client: client as never });
    const el = await screen.findByTestId('queue-health');
    expect(el.className).toContain('semaphore-green');
    expect(el.textContent).toContain('Queue healthy');
    expect(el.textContent).toContain('2 workers');
    expect(el.textContent).toContain('3 queued');
  });

  it('shows YELLOW when Redis is reachable but no worker is running', async () => {
    const status: QueueStatus = {
      ...makeStatus([]),
      redis_reachable: true,
      worker_count: 0,
      queued: 5,
    };
    const client = { getJobs: vi.fn().mockResolvedValue(status) };
    render(JobsPage, { client: client as never });
    const el = await screen.findByTestId('queue-health');
    expect(el.className).toContain('semaphore-yellow');
    expect(el.textContent).toContain('no workers running');
    expect(el.textContent).toContain('5 queued');
  });

  it('shows RED when Redis is unreachable', async () => {
    const status: QueueStatus = {
      available: false,
      redis_reachable: false,
      worker_count: 0,
      queued: 0,
      workers: 0,
      counts: { queued: 0, started: 0, finished: 0, failed: 0, scheduled: 0, deferred: 0 },
      jobs: [],
    };
    const client = { getJobs: vi.fn().mockResolvedValue(status) };
    render(JobsPage, { client: client as never });
    const el = await screen.findByTestId('queue-health');
    expect(el.className).toContain('semaphore-red');
    expect(el.textContent).toContain('offline');
  });

  it('shows RED "limits unavailable" when Redis is unreachable and required (E1)', async () => {
    const status: QueueStatus = {
      available: false,
      redis_reachable: false,
      require_redis: true,
      worker_count: 0,
      queued: 0,
      workers: 0,
      counts: { queued: 0, started: 0, finished: 0, failed: 0, scheduled: 0, deferred: 0 },
      jobs: [],
    };
    const client = { getJobs: vi.fn().mockResolvedValue(status) };
    render(JobsPage, { client: client as never });
    const el = await screen.findByTestId('queue-health');
    expect(el.className).toContain('semaphore-red');
    expect(el.textContent).toContain('limits unavailable');
  });
});

describe('JobsPage admin queue/worker controls (D39)', () => {
  afterEach(() => {
    currentUser.set(null);
    vi.restoreAllMocks();
  });

  it('hides the admin controls for a non-admin', async () => {
    currentUser.set({ id: 'r', username: 'reader', role: 'reader' } as never);
    const client = { getJobs: vi.fn().mockResolvedValue(makeStatus([])) };
    render(JobsPage, { client: client as never });
    await screen.findByTestId('queue-health');
    expect(screen.queryByRole('button', { name: 'Clear queue' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reset workers' })).toBeNull();
  });

  it('clears the pending queue after confirming (admin)', async () => {
    currentUser.set({ id: 'a', username: 'admin', role: 'admin' } as never);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const client = {
      getJobs: vi.fn().mockResolvedValue(makeStatus([])),
      clearQueue: vi.fn().mockResolvedValue({ available: true, dropped: 7 }),
    };
    render(JobsPage, { client: client as never });
    await fireEvent.click(await screen.findByRole('button', { name: 'Clear queue' }));
    await waitFor(() => expect(client.clearQueue).toHaveBeenCalled());
    expect(await screen.findByText(/Dropped 7 pending job/)).toBeTruthy();
  });

  it('resets workers and shows the requeue count + restart hint (admin)', async () => {
    currentUser.set({ id: 'o', username: 'owner', role: 'owner' } as never);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const client = {
      getJobs: vi.fn().mockResolvedValue(makeStatus([])),
      resetWorkers: vi.fn().mockResolvedValue({
        available: true,
        requeued: 2,
        cleared_failed: 3,
        note: 'A full worker process reset is `docker compose restart worker`.',
      }),
    };
    render(JobsPage, { client: client as never });
    await fireEvent.click(await screen.findByRole('button', { name: 'Reset workers' }));
    await waitFor(() => expect(client.resetWorkers).toHaveBeenCalled());
    expect(await screen.findByText(/Requeued 2 stuck job/)).toBeTruthy();
    expect(screen.getByText(/docker compose restart worker/)).toBeTruthy();
  });

  it('does not call the API when the confirm is cancelled', async () => {
    currentUser.set({ id: 'a', username: 'admin', role: 'admin' } as never);
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const client = {
      getJobs: vi.fn().mockResolvedValue(makeStatus([])),
      clearQueue: vi.fn(),
    };
    render(JobsPage, { client: client as never });
    await fireEvent.click(await screen.findByRole('button', { name: 'Clear queue' }));
    expect(client.clearQueue).not.toHaveBeenCalled();
  });
});

describe('JobsPage robustness (issue 2 — freeze / stuck-loading)', () => {
  afterEach(() => {
    currentUser.set(null);
    vi.restoreAllMocks();
  });

  it('shows a Retry (not a permanent "Loading…") when the first load fails, and recovers on retry', async () => {
    const getJobs = vi
      .fn()
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValue(makeStatus([job({ id: 'ok', task: 'recovered' })]));
    const client = { getJobs };
    render(JobsPage, { client: client as never });

    // First load failed → the load-failed placeholder with a Retry button, not "Loading…".
    const retry = await screen.findByRole('button', { name: /retry/i });
    expect(screen.queryByText('Loading…')).toBeNull();
    expect(screen.getByText(/network down/)).toBeTruthy();

    await fireEvent.click(retry);
    expect(await screen.findByText('recovered')).toBeTruthy();
  });

  it('does not throw when the payload is missing counts/jobs (available but partial)', async () => {
    // A partial/older payload with available:true but no counts/jobs must not crash the render
    // (an unguarded status.counts[...] / status.jobs.length used to freeze the tab).
    const partial = { available: true, workers: 1 } as unknown as QueueStatus;
    const client = { getJobs: vi.fn().mockResolvedValue(partial) };
    render(JobsPage, { client: client as never });

    // It renders the counts grid (all = 0) and the empty-jobs note without throwing.
    expect(await screen.findByText('No recent jobs. Import a PDF or click Enrich on a paper to create one.')).toBeTruthy();
    const allBtn = screen.getByTitle('Show jobs of every status') as HTMLElement;
    expect(allBtn.textContent).toContain('0');
  });

  it('keeps the last-good job list visible when a later refresh fails', async () => {
    const getJobs = vi
      .fn()
      .mockResolvedValueOnce(makeStatus([job({ id: 'a', task: 'still-here' })]))
      .mockRejectedValue(new Error('transient blip'));
    const client = { getJobs };
    render(JobsPage, { client: client as never });

    expect(await screen.findByText('still-here')).toBeTruthy();
    await fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    // Error surfaces, but the previously-loaded job stays on screen (tab stays interactive).
    expect(await screen.findByText(/transient blip/)).toBeTruthy();
    expect(screen.getByText('still-here')).toBeTruthy();
  });

  it('explains a positive filter count with no jobs in the recent window', async () => {
    // The "1 failed but the list shows none" case: the count comes from the queue registry while
    // the list is the recent window — surface that instead of a bare "no jobs".
    const status: QueueStatus = {
      ...makeStatus([job({ id: 'f1', task: 'done-job', status: 'finished' })]),
      counts: { queued: 0, started: 0, finished: 1, failed: 2, scheduled: 0, deferred: 0 },
    };
    const client = { getJobs: vi.fn().mockResolvedValue(status) };
    render(JobsPage, { client: client as never });

    await screen.findByText('done-job');
    await fireEvent.click(screen.getByTitle('Show only failed jobs'));
    expect(await screen.findByText(/in the queue registry, but none are in the recent window/)).toBeTruthy();
  });
});

describe('JobsPage newest-first (Phase L, item 9)', () => {
  it('renders jobs in the order the API returns them (newest-first)', async () => {
    // The backend supplies newest-first order; the page must preserve it (no reversal/resort).
    const jobs = [
      job({ id: 'run-new', task: 'newest-running', status: 'started', enqueued_at: '2026-05-01T00:00:00' }),
      job({ id: 'queued', task: 'queued-job', status: 'queued', enqueued_at: '2026-04-01T00:00:00' }),
      job({ id: 'fin-new', task: 'newest-finished', status: 'finished', ended_at: '2026-06-01T00:00:00' }),
      job({ id: 'fin-old', task: 'oldest-finished', status: 'finished', ended_at: '2026-01-01T00:00:00' }),
    ];
    const client = { getJobs: vi.fn().mockResolvedValue(makeStatus(jobs)) };

    render(JobsPage, { client: client as never });

    await waitFor(() => expect(screen.getByText('newest-running')).toBeTruthy());
    const rendered = screen
      .getAllByText(/-running|-finished|queued-job/)
      .map((el) => el.textContent);
    expect(rendered).toEqual(['newest-running', 'queued-job', 'newest-finished', 'oldest-finished']);
  });
});
