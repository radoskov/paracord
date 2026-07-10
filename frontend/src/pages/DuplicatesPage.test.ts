import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DuplicateCandidate, MergePreview } from '../api/client';
import { currentUser } from '../lib/session';
import DuplicatesPage from './DuplicatesPage.svelte';

function workCandidate(overrides: Partial<DuplicateCandidate> = {}): DuplicateCandidate {
  return {
    id: 'cand-1',
    candidate_type: 'fuzzy_title',
    entity_a_type: 'work',
    entity_a_id: 'work-a',
    entity_b_type: 'work',
    entity_b_id: 'work-b',
    score: 0.95,
    signals: {},
    status: 'open',
    created_at: '2026-07-07T00:00:00Z',
    resolved_by_user_id: null,
    resolved_at: null,
    entity_a_label: 'Paper A',
    entity_b_label: 'Paper B',
    suggested_target_work_id: 'work-a',
    summary: null,
    ...overrides,
  };
}

function makePreview(overrides: Partial<MergePreview> = {}): MergePreview {
  return {
    base_work_id: 'work-a',
    source_work_id: 'work-b',
    fill_fields: ['abstract'],
    conflict_fields: ['title'],
    file_count: 2,
    incoming_reference_count: 3,
    will_flatten: false,
    ...overrides,
  };
}

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    listDuplicateCandidates: vi.fn().mockResolvedValue([workCandidate()]),
    getMergePreview: vi.fn().mockResolvedValue(makePreview()),
    applyDuplicateCandidateAction: vi.fn().mockResolvedValue(workCandidate({ status: 'accepted' })),
    updateDuplicateCandidate: vi.fn().mockResolvedValue(workCandidate()),
    scanDuplicateCandidates: vi.fn().mockResolvedValue({ candidates: [], candidate_count: 0 }),
    getJobs: vi.fn().mockResolvedValue({ jobs: [] }),
    // Opening a paper in the WorkDetail modal needs getWork + WorkDetail's load calls.
    getWork: vi.fn().mockResolvedValue({ id: 'work-a', canonical_title: 'Paper A' }),
    listWorkMetadata: vi.fn().mockResolvedValue([]),
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

describe('DuplicatesPage merge/link/swap (Batch D)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u', username: 'ed', role: 'editor' } as never);
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('shows the base / merge-from pair and a merge preview', async () => {
    const client = makeClient();
    render(DuplicatesPage, { client: client as never });
    await screen.findByText('Paper A');
    expect(screen.getByText('Base — merge into')).toBeTruthy();
    expect(screen.getByText('Merge from')).toBeTruthy();
    await waitFor(() =>
      expect(client.getMergePreview).toHaveBeenCalledWith('cand-1', 'work-a'),
    );
    const preview = await screen.findByText(/fills 1 empty field/);
    expect(preview.textContent).toContain('adds 1 conflict');
    expect(preview.textContent).toContain('moves 2 file');
    expect(preview.textContent).toContain('hides the other as a shadow');
  });

  it('merges into the default base (item #1)', async () => {
    const client = makeClient();
    render(DuplicatesPage, { client: client as never });
    await screen.findByText('Paper A');
    await fireEvent.click(screen.getByRole('button', { name: 'Merge' }));
    await waitFor(() =>
      expect(client.applyDuplicateCandidateAction).toHaveBeenCalledWith('cand-1', 'merge_works', {
        targetWorkId: 'work-a',
      }),
    );
  });

  it('swap makes the other paper the base for the merge', async () => {
    const client = makeClient();
    render(DuplicatesPage, { client: client as never });
    await screen.findByText('Paper A');
    await fireEvent.click(
      screen.getByRole('button', { name: 'Swap which paper survives as the base' }),
    );
    await waitFor(() =>
      expect(client.getMergePreview).toHaveBeenCalledWith('cand-1', 'work-b'),
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Merge' }));
    await waitFor(() =>
      expect(client.applyDuplicateCandidateAction).toHaveBeenCalledWith('cand-1', 'merge_works', {
        targetWorkId: 'work-b',
      }),
    );
  });

  it('link relates both papers without moving anything', async () => {
    const client = makeClient();
    render(DuplicatesPage, { client: client as never });
    await screen.findByText('Paper A');
    await fireEvent.click(screen.getByRole('button', { name: 'Link' }));
    await waitFor(() =>
      expect(client.applyDuplicateCandidateAction).toHaveBeenCalledWith('cand-1', 'link_as_version', {
        targetWorkId: 'work-a',
      }),
    );
  });

  it('opens a paper in the paper view when its label is clicked (#2)', async () => {
    const client = makeClient();
    render(DuplicatesPage, { client: client as never });
    await screen.findByText('Paper A');
    // The base/source labels are buttons that open the WorkDetail modal.
    await fireEvent.click(screen.getByRole('button', { name: 'Paper B' }));
    await waitFor(() => expect(client.getWork).toHaveBeenCalledWith('work-b'));
  });

  it('shows an explicit note (not a stuck "Loading…") when the preview fails (#2)', async () => {
    const client = makeClient({
      getMergePreview: vi.fn().mockRejectedValue(new Error('timeout')),
    });
    render(DuplicatesPage, { client: client as never });
    await screen.findByText('Paper A');
    expect(await screen.findByText(/Preview unavailable/)).toBeTruthy();
    expect(screen.queryByText('Loading preview…')).toBeNull();
  });
});

describe('DuplicatesPage duplicates vs multi-work file sub-tabs (#2)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u', username: 'ed', role: 'editor' } as never);
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  function multiworkCandidate(): DuplicateCandidate {
    return workCandidate({
      id: 'cand-mw',
      candidate_type: 'multiwork_file',
      entity_a_type: 'file',
      entity_a_id: 'file-a',
      entity_b_type: 'file',
      entity_b_id: 'file-a',
      entity_a_label: 'bundle.pdf',
      entity_b_label: 'bundle.pdf',
      suggested_target_work_id: null,
    });
  }

  it('shows only duplicate candidates on the Duplicates tab and multi-work ones on their own tab', async () => {
    const client = makeClient({
      listDuplicateCandidates: vi.fn().mockResolvedValue([workCandidate(), multiworkCandidate()]),
    });
    render(DuplicatesPage, { client: client as never });

    // Default tab = Duplicates: the work pair shows, the multi-work file does not.
    await screen.findByText('Paper A');
    expect(screen.queryByText('bundle.pdf')).toBeNull();
    // Counts on each sub-tab.
    expect(screen.getByTestId('dup-tab-duplicates').textContent).toContain('1');
    expect(screen.getByTestId('dup-tab-multiwork').textContent).toContain('1');

    // Switch to the multi-work tab: the file shows, the duplicate pair is hidden.
    await fireEvent.click(screen.getByTestId('dup-tab-multiwork'));
    await screen.findByText('bundle.pdf');
    expect(screen.queryByText('Paper A')).toBeNull();
    expect(screen.getByText(/Split this file into separate papers/)).toBeTruthy();
  });

  it('shows a tab-specific empty message when a sub-tab has no candidates', async () => {
    const client = makeClient({
      listDuplicateCandidates: vi.fn().mockResolvedValue([workCandidate()]),
    });
    render(DuplicatesPage, { client: client as never });
    await screen.findByText('Paper A');
    await fireEvent.click(screen.getByTestId('dup-tab-multiwork'));
    expect(await screen.findByText(/No multi-work file candidates/)).toBeTruthy();
  });
});
