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
    keywordsWork: vi.fn().mockResolvedValue({ job_id: 'j', status: 'queued' }),
    topicWork: vi.fn().mockResolvedValue({ job_id: 'j', status: 'queued' }),
    enrichWork: vi.fn().mockResolvedValue({ job_id: 'j', status: 'queued' }),
    extractWork: vi.fn().mockResolvedValue({ status: 'queued', queued: 1 }),
    bulkApplyMetadata: vi.fn().mockResolvedValue({ field_name: 'title', applied: 2, skipped: 0 }),
    ...overrides,
  };
}

async function selectAll(): Promise<void> {
  await fireEvent.click(screen.getByRole('checkbox', { name: /select all/i }));
}

describe('LibraryPage bulk-action dropdown (issue 3)', () => {
  beforeEach(() => {
    selectedPaperIds.set([]);
    selectedWorkId.set(null);
    pendingLibrarySearch.set(null);
    pendingLibraryOpen.set(null);
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    selectedWorkId.set(null);
  });

  it('runs "Extract keywords" over the whole selection via the dropdown + Go', async () => {
    const client = makeClient();
    render(LibraryPage, { client: client as never });
    await waitFor(() => expect(screen.getByText('Paper One')).toBeTruthy());
    await selectAll();

    const action = screen.getByRole('combobox', { name: /action/i });
    await fireEvent.change(action, { target: { value: 'keywords' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Go' }));

    await waitFor(() => expect(client.keywordsWork).toHaveBeenCalledTimes(2));
    expect(client.keywordsWork).toHaveBeenCalledWith('w1');
    expect(client.keywordsWork).toHaveBeenCalledWith('w2');
  });

  it('"Set metadata from best source" reveals a field picker and calls the bulk endpoint', async () => {
    const client = makeClient();
    render(LibraryPage, { client: client as never });
    await waitFor(() => expect(screen.getByText('Paper One')).toBeTruthy());
    await selectAll();

    const action = screen.getByRole('combobox', { name: /action/i });
    await fireEvent.change(action, { target: { value: 'apply-metadata' } });
    // Field picker appears; default is title.
    const field = await screen.findByRole('combobox', { name: /field/i });
    await fireEvent.change(field, { target: { value: 'venue' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Go' }));

    await waitFor(() =>
      expect(client.bulkApplyMetadata).toHaveBeenCalledWith(['w1', 'w2'], 'venue'),
    );
  });

  it('"Set metadata from best source" supports an "all fields" option', async () => {
    const client = makeClient();
    render(LibraryPage, { client: client as never });
    await waitFor(() => expect(screen.getByText('Paper One')).toBeTruthy());
    await selectAll();

    const action = screen.getByRole('combobox', { name: /action/i });
    await fireEvent.change(action, { target: { value: 'apply-metadata' } });
    const field = await screen.findByRole('combobox', { name: /field/i });
    await fireEvent.change(field, { target: { value: 'all' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Go' }));

    await waitFor(() =>
      expect(client.bulkApplyMetadata).toHaveBeenCalledWith(['w1', 'w2'], 'all'),
    );
  });
});
