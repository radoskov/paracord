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

describe('WorkDetail merge / move (issue 4)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ow', role: 'owner' } as never);
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const page = (items: Work[]) => ({ items, total: items.length, shelves: [], racks: [] });

  it('merges another paper into this one via the picker + preview', async () => {
    const source = makeWork({ id: 'w2', canonical_title: 'Duplicate Paper' });
    const client = makeClient({
      listWorks: vi.fn().mockResolvedValue(page([source])),
      mergePaperPreview: vi.fn().mockResolvedValue({
        base_work_id: 'w1',
        source_work_id: 'w2',
        fill_fields: ['abstract'],
        conflict_fields: [],
        file_count: 1,
        incoming_reference_count: 0,
        will_flatten: false,
      }),
      mergePaper: vi.fn().mockResolvedValue(makeWork()),
    });
    render(WorkDetail, { client: client as never, work: makeWork() });
    await screen.findByText('Attention Is All You Need');

    await fireEvent.click(screen.getByRole('button', { name: 'Merge…' }));
    const input = await screen.findByPlaceholderText(/paper to merge in/i);
    await fireEvent.input(input, { target: { value: 'Duplicate' } });

    await fireEvent.click(await screen.findByText('Duplicate Paper'));
    await waitFor(() => expect(client.mergePaperPreview).toHaveBeenCalledWith('w1', 'w2'));
    expect(await screen.findByText(/1 file moved here/)).toBeTruthy();

    await fireEvent.click(screen.getByRole('button', { name: 'Merge' }));
    await waitFor(() => expect(client.mergePaper).toHaveBeenCalledWith('w1', 'w2'));
  });

  it('moves an attached file to another paper via the picker', async () => {
    const file = {
      id: 'f1',
      sha256: 'a'.repeat(64),
      size_bytes: 10,
      original_filename: 'paper.pdf',
      page_count: null,
      text_layer_quality: 'ok',
      status: 'extracted',
      content_available: true,
    };
    const target = makeWork({ id: 'w2', canonical_title: 'Target Paper' });
    const client = makeClient({
      listWorkFiles: vi.fn().mockResolvedValue([file]),
      listWorks: vi.fn().mockResolvedValue(page([target])),
      moveWorkFile: vi.fn().mockResolvedValue(file),
    });
    render(WorkDetail, { client: client as never, work: makeWork() });
    await screen.findByText('Attention Is All You Need');

    await fireEvent.click(await screen.findByRole('button', { name: 'Move…' }));
    const input = await screen.findByPlaceholderText(/destination paper/i);
    await fireEvent.input(input, { target: { value: 'Target' } });

    await fireEvent.click(await screen.findByText('Target Paper'));
    await waitFor(() => expect(client.moveWorkFile).toHaveBeenCalledWith('w1', 'f1', 'w2'));
  });
});
