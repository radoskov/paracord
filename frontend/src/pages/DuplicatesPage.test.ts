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
});
