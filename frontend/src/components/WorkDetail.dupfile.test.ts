import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Work, WorkFile } from '../api/client';
import { pendingLibrarySearch } from '../lib/selection';
import { currentUser } from '../lib/session';
import WorkDetail from './WorkDetail.svelte';

function makeWork(overrides: Partial<Work> = {}): Work {
  return {
    id: 'w1',
    canonical_title: 'Paper',
    doi: null,
    arxiv_id: null,
    reading_status: 'unread',
    confirmed_fields: [],
    keywords: [],
    topics: [],
    created_by_user_id: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  } as unknown as Work;
}

function makeFile(overrides: Partial<WorkFile> = {}): WorkFile {
  return {
    id: 'f1',
    sha256: 'abc123',
    size_bytes: 100,
    original_filename: 'paper.pdf',
    page_count: 1,
    text_layer_quality: 'good',
    status: 'extracted',
    content_available: true,
    also_in_count: 2,
    ...overrides,
  } as WorkFile;
}

function makeClient(file: WorkFile) {
  return {
    listWorkMetadata: vi.fn().mockResolvedValue([]),
    listWorkFiles: vi.fn().mockResolvedValue([file]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue([]),
    listTags: vi.fn().mockResolvedValue([]),
    listWorkTags: vi.fn().mockResolvedValue([]),
  };
}

describe('WorkDetail duplicate-PDF badge (batch10)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
    pendingLibrarySearch.set(null);
  });

  it('shows a duplicate badge and searches the hash on click', async () => {
    const client = makeClient(makeFile({ also_in_count: 2 }));
    render(WorkDetail, { client: client as never, work: makeWork() });
    await waitFor(() => expect(client.listWorkFiles).toHaveBeenCalled());

    const badge = await screen.findByRole('button', { name: /duplicate PDF/ });
    expect(badge.textContent).toContain('2 others');

    let pending: unknown = null;
    const unsub = pendingLibrarySearch.subscribe((v) => (pending = v));
    await fireEvent.click(badge);
    unsub();
    expect(pending).toEqual({ query: 'abc123', mode: 'metadata' });
  });

  it('shows no duplicate badge when the file is unique', async () => {
    const client = makeClient(makeFile({ also_in_count: 0 }));
    render(WorkDetail, { client: client as never, work: makeWork() });
    await waitFor(() => expect(client.listWorkFiles).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /duplicate PDF/ })).toBeNull();
  });
});
