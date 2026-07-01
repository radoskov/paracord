import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SavedFilter } from '../api/client';
import { pendingLibrarySearch, selectedPaperIds, selectedWorkId } from '../lib/selection';
import LibraryPage from './LibraryPage.svelte';

const SAVED: SavedFilter = {
  id: 'sf-1',
  name: 'Recent unread',
  search_mode: 'metadata',
  query_text: 'transformer',
  params: { reading_status: 'unread', missing: ['doi'] },
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
};

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    listShelves: vi.fn().mockResolvedValue([]),
    listRacks: vi.fn().mockResolvedValue([]),
    listTags: vi.fn().mockResolvedValue([]),
    listSavedFilters: vi.fn().mockResolvedValue([SAVED]),
    getPreferences: vi.fn().mockResolvedValue({}),
    listWorks: vi.fn().mockResolvedValue([]),
    createSavedFilter: vi.fn().mockResolvedValue({ ...SAVED, id: 'sf-2', name: 'New filter' }),
    deleteSavedFilter: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe('LibraryPage saved filters (Phase B7)', () => {
  beforeEach(() => {
    selectedPaperIds.set([]);
    selectedWorkId.set(null);
    pendingLibrarySearch.set(null);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    selectedPaperIds.set([]);
  });

  it('loads saved filters on mount', async () => {
    const client = makeClient();
    render(LibraryPage, { client: client as never });
    await waitFor(() => expect(client.listWorks).toHaveBeenCalled());
    // The saved filter appears in the "Apply saved filter" dropdown.
    const select = screen.getByLabelText('Apply saved filter') as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(select.options).map((o) => o.textContent)).toContain('Recent unread'),
    );
  });

  it('save current filter prompts for a name and calls createSavedFilter', async () => {
    const client = makeClient();
    vi.spyOn(window, 'prompt').mockReturnValue('New filter');
    render(LibraryPage, { client: client as never });
    await waitFor(() => expect(client.listSavedFilters).toHaveBeenCalled());

    // Type a search so the created filter carries it through.
    const searchInput = screen.getByLabelText('Search') as HTMLInputElement;
    await fireEvent.input(searchInput, { target: { value: 'attention' } });

    await fireEvent.click(screen.getByRole('button', { name: /save current filter/i }));

    await waitFor(() => expect(client.createSavedFilter).toHaveBeenCalled());
    const payload = client.createSavedFilter.mock.calls[0][0];
    expect(payload.name).toBe('New filter');
    expect(payload.search_mode).toBe('metadata');
    expect(payload.query_text).toBe('attention');
    expect(payload.params.missing).toEqual([]);
  });

  it('applying a saved filter sets search/mode/params and reloads the works', async () => {
    const client = makeClient();
    render(LibraryPage, { client: client as never });
    // Wait for the mount's own loadWorks to complete, then clear so we only see the apply reload.
    await waitFor(() => expect(client.listWorks).toHaveBeenCalled());
    client.listWorks.mockClear();

    const select = screen.getByLabelText('Apply saved filter') as HTMLSelectElement;
    await fireEvent.change(select, { target: { value: 'sf-1' } });

    // Applying reloads the works with the filter's query_text + stored structured params
    // (metadata mode) — the search box and structured filters are all hydrated from the filter.
    await waitFor(() =>
      expect(client.listWorks).toHaveBeenCalledWith(
        expect.objectContaining({ q: 'transformer', readingStatus: 'unread', missing: ['doi'] }),
      ),
    );
  });
});
