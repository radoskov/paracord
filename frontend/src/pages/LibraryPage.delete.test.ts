import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Work } from '../api/client';
import { pendingLibraryOpen, pendingLibrarySearch, selectedPaperIds, selectedWorkId } from '../lib/selection';
import { currentUser } from '../lib/session';
import LibraryPage from './LibraryPage.svelte';

function work(id: string, title: string): Work {
  return {
    id,
    canonical_title: title,
    reading_status: 'unread',
    confirmed_fields: [],
    keywords: [],
    topics: [],
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
  } as unknown as Work;
}

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    listShelves: vi.fn().mockResolvedValue([]),
    listRacks: vi.fn().mockResolvedValue([]),
    listTags: vi.fn().mockResolvedValue([]),
    listSavedFilters: vi.fn().mockResolvedValue([]),
    getPreferences: vi.fn().mockResolvedValue({}),
    listWorks: vi.fn().mockResolvedValue({
      items: [work('w1', 'Paper One'), work('w2', 'Paper Two')],
      total: 2,
      page: 1,
      pages: 1,
      per_page: 100,
    }),
    // WorkDetail dependencies for the opened paper.
    listWorkMetadata: vi.fn().mockResolvedValue([]),
    listWorkFiles: vi.fn().mockResolvedValue([]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue([]),
    listWorkTags: vi.fn().mockResolvedValue([]),
    getJobs: vi.fn().mockResolvedValue({ available: false, workers: 0, counts: {}, jobs: [] }),
    getWork: vi.fn().mockResolvedValue(work('w1', 'Paper One')),
    deleteWork: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe('LibraryPage delete removes the row immediately (P1b)', () => {
  beforeEach(() => {
    selectedPaperIds.set([]);
    selectedWorkId.set(null);
    pendingLibrarySearch.set(null);
    pendingLibraryOpen.set(null);
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true));
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    selectedWorkId.set(null);
  });

  it('drops the deleted paper from the list and decrements the counter', async () => {
    const client = makeClient();
    render(LibraryPage, { client: client as never });
    await waitFor(() => expect(screen.getByText('Paper One')).toBeTruthy());
    expect(screen.getByText(/2 papers/)).toBeTruthy();

    // Open Paper One, then delete it from the detail panel.
    await fireEvent.click(screen.getByText('Paper One').closest('tr') as HTMLTableRowElement);
    const del = await screen.findByRole('button', { name: 'Delete' });
    await fireEvent.click(del);

    await waitFor(() => expect(client.deleteWork).toHaveBeenCalledWith('w1'));
    // Row is gone from the list without a reload...
    await waitFor(() => expect(screen.queryByText('Paper One')).toBeNull());
    expect(screen.getByText('Paper Two')).toBeTruthy();
    // ...and the counter reflects the removal.
    expect(screen.getByText(/1 papers/)).toBeTruthy();
    // No refetch was needed to update the view.
    expect(client.listWorks).toHaveBeenCalledTimes(1);
  });
});
