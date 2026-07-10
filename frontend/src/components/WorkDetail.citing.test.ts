import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Work } from '../api/client';
import { currentUser } from '../lib/session';
import WorkDetail from './WorkDetail.svelte';

function makeWork(overrides: Partial<Work> = {}): Work {
  return {
    id: 'w1',
    canonical_title: 'Attention Is All You Need',
    doi: '10.1/base',
    arxiv_id: null,
    venue: null,
    year: 2017,
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

function makeClient(over: Record<string, unknown> = {}) {
  return {
    listWorkMetadata: vi.fn().mockResolvedValue([]),
    listWorkFiles: vi.fn().mockResolvedValue([]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue([]),
    listTags: vi.fn().mockResolvedValue([]),
    listWorkTags: vi.fn().mockResolvedValue([]),
    getCitingPapers: vi.fn().mockResolvedValue({
      items: [],
      source: null,
      fetched_at: null,
      citation_count: 9,
      citation_count_source: 'crossref',
    }),
    fetchCitingPapers: vi.fn().mockResolvedValue({
      items: [
        { id: 'c1', source: 'openalex', external_id: 'W2', title: 'A Citing Paper', authors: 'Jane Roe', year: 2022, doi: '10.1/citing', venue: 'Journal' },
      ],
      source: 'openalex',
      fetched_at: '2026-07-10T00:00:00Z',
      citation_count: 9,
      citation_count_source: 'crossref',
    }),
    ...over,
  };
}

describe('WorkDetail citing-papers panel (batch10 #8)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
  });

  it('loads the cached list on open and fetches citing papers on demand', async () => {
    const client = makeClient();
    render(WorkDetail, { client: client as never, work: makeWork() });

    // Open the "Citing papers" panel → loads the cached (empty) list.
    const summary = screen.getByText(/Citing papers/);
    await fireEvent.click(summary);
    await waitFor(() => expect(client.getCitingPapers).toHaveBeenCalledWith('w1'));

    // Fetch on demand → renders the fetched citing paper.
    await fireEvent.click(screen.getByRole('button', { name: 'Fetch citing papers' }));
    await waitFor(() => expect(client.fetchCitingPapers).toHaveBeenCalledWith('w1'));
    await waitFor(() => expect(screen.getByText('A Citing Paper')).toBeTruthy());
  });

  it('disables the fetch button when the paper has no DOI or arXiv id', async () => {
    const client = makeClient();
    render(WorkDetail, { client: client as never, work: makeWork({ doi: null, arxiv_id: null }) });
    await fireEvent.click(screen.getByText(/Citing papers/));
    await waitFor(() => expect(client.getCitingPapers).toHaveBeenCalled());
    const btn = screen.getByRole('button', { name: 'Fetch citing papers' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
