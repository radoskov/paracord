import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Work } from '../api/client';
import { currentUser } from '../lib/session';
import WorkDetail from './WorkDetail.svelte';

function makeWork(overrides: Partial<Work> = {}): Work {
  return {
    id: 'w1',
    canonical_title: 'Attention Is All You Need',
    abstract: 'transformers',
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
    getRelatedWorks: vi.fn().mockResolvedValue([]),
    getRelatedLinks: vi.fn().mockResolvedValue([makeWork({ id: 'w2', canonical_title: 'Linked Paper' })]),
    unmergePaper: vi.fn().mockResolvedValue(makeWork({ has_reversible_shadow: false })),
    ...overrides,
  };
}

describe('WorkDetail unmerge + linked papers (Batch D)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ow', role: 'owner' } as never);
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the Unmerge button only when the paper has a reversible shadow', async () => {
    const client = makeClient();
    const { unmount } = render(WorkDetail, {
      client: client as never,
      work: makeWork({ has_reversible_shadow: false }),
    });
    await screen.findByText('Attention Is All You Need');
    expect(screen.queryByRole('button', { name: 'Unmerge' })).toBeNull();
    unmount();

    render(WorkDetail, {
      client: client as never,
      work: makeWork({ has_reversible_shadow: true }),
    });
    const btn = await screen.findByRole('button', { name: 'Unmerge' });
    await fireEvent.click(btn);
    await waitFor(() => expect(client.unmergePaper).toHaveBeenCalledWith('w1'));
  });

  it('loads linked papers when the section opens', async () => {
    const client = makeClient();
    render(WorkDetail, { client: client as never, work: makeWork() });
    const summary = await screen.findByText('Linked papers');
    const details = summary.closest('details') as HTMLDetailsElement;
    details.open = true;
    await fireEvent(details, new Event('toggle'));
    await waitFor(() => expect(client.getRelatedLinks).toHaveBeenCalledWith('w1'));
    await screen.findByText('Linked Paper');
  });
});
