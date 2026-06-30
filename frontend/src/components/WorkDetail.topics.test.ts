import { fireEvent, render, screen } from '@testing-library/svelte';
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
    keywords: ['self attention', 'transformer architecture'],
    topics: ['attention', 'transformer'],
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
    topicWork: vi.fn().mockResolvedValue({ job_id: 'jt', status: 'queued' }),
    keywordsWork: vi.fn().mockResolvedValue({ job_id: 'jk', status: 'queued' }),
    ...overrides,
  };
}

describe('WorkDetail topics & keyword actions (Phase K)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
  });

  it('renders topics chips separately from keyword chips, under a Topics label', () => {
    render(WorkDetail, { client: makeClient() as never, work: makeWork() });

    // Both keyword and topic chips render.
    expect(screen.getByRole('button', { name: 'self attention' })).toBeTruthy();
    const topicChip = screen.getByRole('button', { name: 'attention' });
    expect(topicChip).toBeTruthy();

    // The topic chip lives inside the separated .topics block (own class + a "Topics" label),
    // not inside the .keywords block — i.e. visually distinct.
    expect(screen.getByText('Topics')).toBeTruthy();
    expect(topicChip.classList.contains('topic')).toBe(true);
    expect(topicChip.closest('.topics')).not.toBeNull();
    expect(topicChip.closest('.keywords')).toBeNull();
  });

  it('Topic and Keyword buttons trigger the client methods when the user may modify', async () => {
    const client = makeClient();
    render(WorkDetail, { client: client as never, work: makeWork() });

    await fireEvent.click(screen.getByRole('button', { name: 'Topic' }));
    await fireEvent.click(screen.getByRole('button', { name: 'Keyword' }));
    expect(client.topicWork).toHaveBeenCalledWith('w1');
    expect(client.keywordsWork).toHaveBeenCalledWith('w1');
  });

  it('gates the Topic/Keyword buttons on modify rights (contributor on someone else’s paper)', () => {
    currentUser.set({ id: 'u1', username: 'co', role: 'contributor' } as never);
    // Paper owned by a different user -> contributor may not modify it.
    const work = makeWork({ created_by_user_id: 'someone-else' });
    render(WorkDetail, { client: makeClient() as never, work });

    const topicBtn = screen.getByRole('button', { name: 'Topic' }) as HTMLButtonElement;
    const keywordBtn = screen.getByRole('button', { name: 'Keyword' }) as HTMLButtonElement;
    expect(topicBtn.disabled).toBe(true);
    expect(keywordBtn.disabled).toBe(true);
  });
});
