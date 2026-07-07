import { render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Work } from '../api/client';
import { currentUser } from '../lib/session';
import WorkDetail from './WorkDetail.svelte';

function makeWork(overrides: Partial<Work> = {}): Work {
  return {
    id: 'w1',
    canonical_title: 'Attention Is All You Need',
    abstract: 'original abstract',
    doi: null,
    arxiv_id: null,
    venue: null,
    year: 2017,
    reading_status: 'unread',
    canonical_metadata_source: null,
    confirmed_fields: [],
    keywords: [],
    topics: [],
    created_by_user_id: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  } as unknown as Work;
}

function jobs(status: string) {
  return {
    available: true,
    workers: 1,
    counts: {},
    jobs: [
      {
        id: 'j1',
        task: 'enrich',
        status,
        enqueued_at: null,
        ended_at: null,
        error: null,
        target_kind: 'work',
        target_id: 'w1',
      },
    ],
  };
}

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    listWorkMetadata: vi.fn().mockResolvedValue([]),
    listWorkFiles: vi.fn().mockResolvedValue([]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue([]),
    listTags: vi.fn().mockResolvedValue([]),
    listWorkTags: vi.fn().mockResolvedValue([]),
    getJobs: vi.fn().mockResolvedValue({ available: true, workers: 1, counts: {}, jobs: [] }),
    getWork: vi.fn().mockResolvedValue(makeWork()),
    ...overrides,
  };
}

describe('WorkDetail live-refresh on background-job completion (P1a)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('refetches the open paper once its in-flight job settles', async () => {
    const getJobs = vi
      .fn()
      .mockResolvedValueOnce(jobs('started'))
      .mockResolvedValue(jobs('finished'));
    const getWork = vi.fn().mockResolvedValue(makeWork({ abstract: 'refreshed abstract' }));
    const client = makeClient({ getJobs, getWork });
    render(WorkDetail, { client: client as never, work: makeWork() });

    // Flush the initial load, which arms the poller.
    await vi.advanceTimersByTimeAsync(0);
    // First poll: the job is still running — no refetch yet.
    await vi.advanceTimersByTimeAsync(4000);
    expect(getJobs).toHaveBeenCalledTimes(1);
    expect(getWork).not.toHaveBeenCalled();
    // Second poll: the job has finished — the open paper is refetched.
    await vi.advanceTimersByTimeAsync(4000);
    expect(getWork).toHaveBeenCalledWith('w1');
    // The refreshed abstract lands in the editable form.
    expect((screen.getByLabelText('Abstract') as HTMLTextAreaElement).value).toBe(
      'refreshed abstract',
    );
  });

  it('does not refetch when no job is in flight for the open paper', async () => {
    const getWork = vi.fn().mockResolvedValue(makeWork());
    const client = makeClient({ getWork });
    render(WorkDetail, { client: client as never, work: makeWork() });

    await vi.advanceTimersByTimeAsync(0);
    // One poll finds nothing pending and stops without a spurious refetch.
    await vi.advanceTimersByTimeAsync(4000);
    expect(getWork).not.toHaveBeenCalled();
    // Polling has stopped: further time passes with no additional job queries.
    await vi.advanceTimersByTimeAsync(8000);
    expect(client.getJobs).toHaveBeenCalledTimes(1);
  });
});
