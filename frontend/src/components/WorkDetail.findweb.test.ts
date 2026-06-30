import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { WebFindResponse, Work } from '../api/client';
import { currentUser } from '../lib/session';
import WorkDetail from './WorkDetail.svelte';

const WORK: Work = {
  id: 'w1',
  canonical_title: 'Deep Residual Learning',
  abstract: null,
  doi: null,
  arxiv_id: null,
  venue: null,
  year: 2016,
  reading_status: 'unread',
  canonical_metadata_source: null,
  confirmed_fields: [],
  keywords: [],
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
} as unknown as Work;

const SEARCH: WebFindResponse = {
  candidates: [
    {
      candidate_id: 'c1',
      source: 'openalex',
      sources: ['openalex'],
      title: 'Deep Residual Learning for Image Recognition',
      authors: ['Kaiming He'],
      year: 2016,
      doi: '10.1/x',
      pdf_url: 'https://arxiv.org/pdf/1512.03385.pdf',
      landing_url: 'https://arxiv.org/abs/1512.03385',
      is_oa: true,
      score: 0.95,
    },
    {
      candidate_id: 'c2',
      source: 'crossref',
      sources: ['crossref'],
      title: 'Another Candidate',
      authors: [],
      year: 2015,
      doi: '10.1/y',
      pdf_url: null,
      landing_url: 'https://doi.org/10.1/y',
      is_oa: false,
      score: 0.4,
    },
  ],
  degraded_sources: ['semanticscholar'],
  queried_sources: ['openalex', 'crossref', 'semanticscholar'],
};

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    listWorkMetadata: vi.fn().mockResolvedValue([]),
    listWorkFiles: vi.fn().mockResolvedValue([]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue([]),
    listTags: vi.fn().mockResolvedValue([]),
    findOnWeb: vi.fn().mockResolvedValue(SEARCH),
    downloadWebCandidates: vi.fn(),
    ...overrides,
  };
}

describe('WorkDetail find-on-web picker', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
  });

  it('opens the picker, gates Download by selection, and merges per-row status', async () => {
    const downloadWebCandidates = vi.fn().mockResolvedValue({
      results: [
        { candidate_id: 'c1', status: 'attached', reason: null, file: null },
      ],
    });
    const client = makeClient({ downloadWebCandidates });
    render(WorkDetail, { client: client as never, work: WORK });

    await fireEvent.click(screen.getByRole('button', { name: /find on web/i }));
    await waitFor(() => expect(client.findOnWeb).toHaveBeenCalledWith('w1'));

    // Both candidates render; degraded source is surfaced.
    expect(await screen.findByText(/Deep Residual Learning for Image Recognition/)).toBeTruthy();
    expect(screen.getByText(/semanticscholar/)).toBeTruthy();

    // Download is disabled with nothing selected.
    const downloadBtn = screen.getByRole('button', { name: /download selected/i }) as HTMLButtonElement;
    expect(downloadBtn.disabled).toBe(true);

    // Select the first candidate → Download enables, label shows the count.
    const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    await fireEvent.click(checkboxes[0]);
    expect(downloadBtn.disabled).toBe(false);
    expect(downloadBtn.textContent).toContain('(1)');

    await fireEvent.click(downloadBtn);
    await waitFor(() =>
      expect(downloadWebCandidates).toHaveBeenCalledWith('w1', [
        { candidate_id: 'c1', url: 'https://arxiv.org/pdf/1512.03385.pdf', source: 'openalex' },
      ]),
    );
    expect(await screen.findByText(/Attached/)).toBeTruthy();
  });

  it('shows a manual-upload fallback when a download cannot complete', async () => {
    const downloadWebCandidates = vi.fn().mockResolvedValue({
      results: [
        { candidate_id: 'c1', status: 'manual_upload_needed', reason: 'login wall', file: null },
      ],
    });
    const client = makeClient({ downloadWebCandidates });
    render(WorkDetail, { client: client as never, work: WORK });

    await fireEvent.click(screen.getByRole('button', { name: /find on web/i }));
    await screen.findByText(/Deep Residual Learning for Image Recognition/);
    const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    await fireEvent.click(checkboxes[0]);
    await fireEvent.click(screen.getByRole('button', { name: /download selected/i }));

    expect(await screen.findByText(/Could not download automatically/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Upload the PDF manually/i })).toBeTruthy();
  });
});
