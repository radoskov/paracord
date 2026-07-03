import { fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { FieldReview, Work } from '../api/client';
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

const CONFLICT_FIELD: FieldReview = {
  field_name: 'title',
  canonical_value: 'Title A',
  has_conflict: true,
  confirmed: false,
  assertions: [
    { id: 'a1', field_name: 'title', value: 'Title A', source: 'crossref', confidence: null, selected_as_canonical: true },
    { id: 'a2', field_name: 'title', value: 'Title B', source: 'openalex', confidence: null, selected_as_canonical: false },
  ],
};

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    listWorkMetadata: vi.fn().mockResolvedValue([CONFLICT_FIELD]),
    deleteMetadataAssertion: vi.fn().mockResolvedValue(makeWork()),
    selectMetadataAssertion: vi.fn().mockResolvedValue(makeWork()),
    listWorkFiles: vi.fn().mockResolvedValue([]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue([]),
    listTags: vi.fn().mockResolvedValue([]),
    listWorkTags: vi.fn().mockResolvedValue([]),
    ...overrides,
  };
}

describe('WorkDetail metadata-conflict remove (Phase L, item 8)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders a Remove button per assertion and calls deleteMetadataAssertion on confirm', async () => {
    const client = makeClient();
    render(WorkDetail, { client: client as never, work: makeWork() });

    // Wait for the async metadata load to render the assertions.
    await screen.findByText('Title B');
    // Remove buttons that live inside a metadata .assertion row (excludes the unrelated tag
    // "Remove" button elsewhere in the panel).
    const removeButtons = screen
      .getAllByRole('button', { name: 'Remove' })
      .filter((btn) => btn.closest('.assertion') !== null);
    // One per assertion (both the canonical and the alternative can be removed).
    expect(removeButtons.length).toBe(2);

    await fireEvent.click(removeButtons[1]);
    expect(client.deleteMetadataAssertion).toHaveBeenCalledWith('w1', 'a2');
    // Field list is refreshed after a removal (initial load + post-delete refresh).
    expect(client.listWorkMetadata.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('does not call deleteMetadataAssertion when the confirm is dismissed', async () => {
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(false));
    const client = makeClient();
    render(WorkDetail, { client: client as never, work: makeWork() });

    await screen.findByText('Title B');
    const removeButton = screen
      .getAllByRole('button', { name: 'Remove' })
      .find((btn) => btn.closest('.assertion') !== null)!;
    await fireEvent.click(removeButton);
    expect(client.deleteMetadataAssertion).not.toHaveBeenCalled();
  });

  it('disables the Remove button for a contributor who may not modify the paper', async () => {
    currentUser.set({ id: 'u1', username: 'co', role: 'contributor' } as never);
    const work = makeWork({ created_by_user_id: 'someone-else' });
    render(WorkDetail, { client: makeClient() as never, work });

    await screen.findByText('Title B');
    const removeButtons = screen
      .getAllByRole('button', { name: 'Remove' })
      .filter((btn) => btn.closest('.assertion') !== null);
    expect(removeButtons.length).toBe(2);
    for (const btn of removeButtons) {
      expect((btn as HTMLButtonElement).disabled).toBe(true);
    }
  });
});
