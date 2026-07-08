import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Work } from '../api/client';
import { currentUser } from '../lib/session';
import WorkDetail from './WorkDetail.svelte';

function makeWork(overrides: Partial<Work> = {}): Work {
  return {
    id: 'w1',
    canonical_title: 'Attention Is All You Need',
    abstract: 'transformers and attention',
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

function makeClient() {
  return {
    listWorkMetadata: vi.fn().mockResolvedValue([]),
    listWorkFiles: vi.fn().mockResolvedValue([]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue([]),
    listTags: vi.fn().mockResolvedValue([]),
    listWorkTags: vi.fn().mockResolvedValue([]),
  };
}

describe('WorkDetail citation count (Track C P1)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
  });

  it('shows the count with its source and as-of date when present', () => {
    render(WorkDetail, {
      client: makeClient() as never,
      work: makeWork({
        citation_count: 201457,
        citation_count_source: 'openalex',
        citation_count_fetched_at: '2026-07-02T00:00:00Z',
      }),
    });
    const block = screen.getByTestId('citation-count');
    expect(block.textContent).toContain('201,457');
    expect(block.textContent).toContain('via openalex');
    expect(block.textContent?.toLowerCase()).toContain('as of');
  });

  it('shows a graceful dash when there is no citation count', () => {
    render(WorkDetail, { client: makeClient() as never, work: makeWork() });
    const block = screen.getByTestId('citation-count');
    expect(block.textContent).toContain('—');
    expect(block.textContent).not.toContain('via');
  });

  it('an "in library" reference is a clickable link that navigates to the paper (issue 10)', async () => {
    const client = makeClient();
    client.listWorkReferences = vi.fn().mockResolvedValue([
      {
        id: 'r1',
        title: 'Resolved Reference',
        raw_citation: null,
        doi: null,
        arxiv_id: null,
        year: 2015,
        resolved_work_id: 'w-target',
      },
    ]);
    const onSelectWork = vi.fn();
    render(WorkDetail, { client: client as never, work: makeWork(), onSelectWork });

    const badge = await screen.findByRole('button', { name: /in library/i });
    await fireEvent.click(badge);
    await waitFor(() => expect(onSelectWork).toHaveBeenCalledWith('w-target'));
  });
});
