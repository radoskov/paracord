import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Work } from '../api/client';
import { pendingLibraryOpen, pendingLibrarySearch, selectedPaperIds, selectedWorkId } from '../lib/selection';
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

describe('LibraryPage jump-to-open (L3)', () => {
  beforeEach(() => {
    selectedPaperIds.set([]);
    selectedWorkId.set(null);
    pendingLibrarySearch.set(null);
    pendingLibraryOpen.set(null);
    // jsdom has no scrollIntoView.
    (HTMLElement.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    selectedWorkId.set(null);
  });

  it('scrolls the open paper row into view and flashes it', async () => {
    const client = makeClient();
    render(LibraryPage, { client: client as never });
    await waitFor(() => expect(client.listWorks).toHaveBeenCalled());

    // Open a paper by clicking its row.
    const row = screen.getByText('Paper One').closest('tr') as HTMLTableRowElement;
    await fireEvent.click(row);

    const jump = screen.getByRole('button', { name: /jump to open/i });
    expect((jump as HTMLButtonElement).disabled).toBe(false);
    await fireEvent.click(jump);

    expect(
      (HTMLElement.prototype as unknown as { scrollIntoView: ReturnType<typeof vi.fn> })
        .scrollIntoView,
    ).toHaveBeenCalled();
    await waitFor(() => expect(row.classList.contains('flash')).toBe(true));
  });

  it('explains when the open paper is not on the current page', async () => {
    // First load has the paper; after a filter change it drops out of the result set while staying
    // the open paper (loadWorks keeps `selected` even when it's no longer listed).
    const listWorks = vi
      .fn()
      .mockResolvedValueOnce({
        items: [work('w1', 'Paper One'), work('w2', 'Paper Two')],
        total: 2,
        page: 1,
        pages: 1,
        per_page: 100,
      })
      .mockResolvedValue({
        items: [work('w3', 'Paper Three')],
        total: 1,
        page: 1,
        pages: 1,
        per_page: 100,
      });
    const client = makeClient({ listWorks });
    render(LibraryPage, { client: client as never });
    await waitFor(() => expect(screen.getByText('Paper One')).toBeTruthy());

    // Open Paper One, then re-run the toolbar search so the list reloads to a set without it — it
    // stays the open paper (its title still shows in the detail pane) but has no row on this page.
    await fireEvent.click(screen.getByText('Paper One').closest('tr') as HTMLTableRowElement);
    const searchForm = (screen.getByLabelText('Search') as HTMLInputElement).closest(
      'form',
    ) as HTMLFormElement;
    await fireEvent.submit(searchForm);
    await waitFor(() => expect(listWorks).toHaveBeenCalledTimes(2), { timeout: 3000 });
    await waitFor(() => expect(screen.getByText('Paper Three')).toBeTruthy(), { timeout: 3000 });

    await fireEvent.click(screen.getByRole('button', { name: /jump to open/i }));
    await waitFor(() => expect(screen.getByText(/isn.t on this page/i)).toBeTruthy(), {
      timeout: 3000,
    });
  });
});
