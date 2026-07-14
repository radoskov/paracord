import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Work } from '../api/client';
import {
  pendingLibraryOpen,
  pendingLibrarySearch,
  selectedPaperIds,
  selectedWorkId,
} from '../lib/selection';
import LibraryPage from './LibraryPage.svelte';

function work(id: string, title: string): Work {
  return {
    id,
    canonical_title: title,
    reading_status: 'unread',
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
  } as Work;
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
    ...overrides,
  };
}

describe('LibraryPage paper-view history (Previous button)', () => {
  beforeEach(() => {
    selectedPaperIds.set([]);
    selectedWorkId.set(null);
    pendingLibrarySearch.set(null);
    pendingLibraryOpen.set(null);
    (HTMLElement.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    selectedWorkId.set(null);
  });

  it('walks back to the previously opened paper and hides the button when history is empty', async () => {
    const client = makeClient();
    render(LibraryPage, { client: client as never });
    await waitFor(() => expect(client.listWorks).toHaveBeenCalled());

    await fireEvent.click(screen.getByText('Paper One').closest('tr') as HTMLTableRowElement);
    // First open — nothing to go back to.
    expect(screen.queryByTestId('detail-back')).toBeNull();

    await fireEvent.click(screen.getByText('Paper Two').closest('tr') as HTMLTableRowElement);
    expect(get(selectedWorkId)).toBe('w2');

    await fireEvent.click(await screen.findByTestId('detail-back'));
    expect(get(selectedWorkId)).toBe('w1');
    // History exhausted — the button disappears.
    await waitFor(() => expect(screen.queryByTestId('detail-back')).toBeNull());
  });

  it('refetches a previous paper that left the current list', async () => {
    const client = makeClient({
      getWork: vi.fn().mockResolvedValue(work('w0', 'Paper Zero')),
    });
    render(LibraryPage, { client: client as never });
    // Wait for the list to actually render (not just the fetch) so the initial load's
    // restore-remembered-selection step can't race the pending-open below.
    await screen.findByText('Paper Two');

    // Open an off-list paper (as a citation-graph click would), then an in-list one.
    pendingLibraryOpen.set('w0');
    await waitFor(() => expect(get(selectedWorkId)).toBe('w0'));
    await fireEvent.click(screen.getByText('Paper Two').closest('tr') as HTMLTableRowElement);
    await waitFor(() => expect(get(selectedWorkId)).toBe('w2'));

    await fireEvent.click(await screen.findByTestId('detail-back'));
    await waitFor(() => expect(get(selectedWorkId)).toBe('w0'));
    expect(client.getWork).toHaveBeenLastCalledWith('w0');
  });
});
