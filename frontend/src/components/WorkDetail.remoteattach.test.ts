/**
 * Files panel "From URL…" / "From server path…" attach modal.
 *
 * The URL mode rides the find-on-web download endpoint (one synthetic `manual_url` item) so the
 * whole download policy applies — including the needs_confirmation handshake for unknown hosts in
 * `unrestricted` mode. The path mode calls the from-path endpoint and surfaces its refusals
 * verbatim (path outside the allowed roots, not a PDF, …).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Work } from '../api/client';
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
    getJobs: vi.fn().mockResolvedValue({ available: false, workers: 0, counts: {}, jobs: [] }),
    getWork: vi.fn().mockResolvedValue(WORK),
    downloadWebCandidates: vi.fn(),
    attachWorkFileFromPath: vi.fn(),
    ...overrides,
  };
}

async function openModal(name: RegExp): Promise<void> {
  await fireEvent.click(screen.getByRole('button', { name }));
}

describe('WorkDetail attach from URL / server path', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
  });

  it('URL mode: sends a manual_url item, reports success, OK closes the modal', async () => {
    const downloadWebCandidates = vi.fn().mockResolvedValue({
      results: [{ candidate_id: 'manual-url', status: 'attached', reason: null, file: null }],
    });
    const client = makeClient({ downloadWebCandidates });
    render(WorkDetail, { client: client as never, work: WORK });

    await openModal(/from url/i);
    const input = screen.getByLabelText('PDF URL');
    await fireEvent.input(input, { target: { value: 'https://example.org/p.pdf' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Proceed' }));

    await waitFor(() =>
      expect(downloadWebCandidates).toHaveBeenCalledWith('w1', [
        {
          candidate_id: 'manual-url',
          url: 'https://example.org/p.pdf',
          source: 'manual_url',
          confirmed: false,
        },
      ]),
    );
    expect(await screen.findByText(/PDF fetched and attached/)).toBeTruthy();
    // The paper is refetched (doi/arxiv backfill + queued extraction).
    expect(client.getWork).toHaveBeenCalledWith('w1');

    await fireEvent.click(screen.getByRole('button', { name: 'OK' }));
    await waitFor(() => expect(screen.queryByLabelText('PDF URL')).toBeNull());
  });

  it('URL mode: needs_confirmation shows the warning, "Download anyway" re-sends confirmed', async () => {
    const downloadWebCandidates = vi
      .fn()
      .mockResolvedValueOnce({
        results: [
          {
            candidate_id: 'manual-url',
            status: 'needs_confirmation',
            reason: 'Unknown host example.org.',
            url: 'https://example.org/p.pdf',
            file: null,
          },
        ],
      })
      .mockResolvedValueOnce({
        results: [{ candidate_id: 'manual-url', status: 'attached', reason: null, file: null }],
      });
    const client = makeClient({ downloadWebCandidates });
    render(WorkDetail, { client: client as never, work: WORK });

    await openModal(/from url/i);
    await fireEvent.input(screen.getByLabelText('PDF URL'), {
      target: { value: 'https://example.org/p.pdf' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Proceed' }));

    expect(await screen.findByText(/Unknown host example\.org/)).toBeTruthy();
    await fireEvent.click(screen.getByRole('button', { name: /download anyway/i }));

    await waitFor(() => expect(downloadWebCandidates).toHaveBeenCalledTimes(2));
    expect(downloadWebCandidates.mock.calls[1][1][0]).toMatchObject({ confirmed: true });
    expect(await screen.findByText(/PDF fetched and attached/)).toBeTruthy();
  });

  it('URL mode: a blocked/error result surfaces the backend reason', async () => {
    const downloadWebCandidates = vi.fn().mockResolvedValue({
      results: [
        {
          candidate_id: 'manual-url',
          status: 'blocked',
          reason: 'refused: shadow-library host',
          file: null,
        },
      ],
    });
    const client = makeClient({ downloadWebCandidates });
    render(WorkDetail, { client: client as never, work: WORK });

    await openModal(/from url/i);
    await fireEvent.input(screen.getByLabelText('PDF URL'), {
      target: { value: 'https://bad.example/p.pdf' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Proceed' }));

    expect(await screen.findByText(/shadow-library host/)).toBeTruthy();
    // Still recoverable: Proceed/Cancel remain (no OK-success state).
    expect(screen.getByRole('button', { name: 'Proceed' })).toBeTruthy();
  });

  it('path mode: calls the from-path endpoint, reports success, refreshes the file list', async () => {
    const attachWorkFileFromPath = vi
      .fn()
      .mockResolvedValue({ id: 'f1', original_filename: 'paper.pdf' });
    const client = makeClient({ attachWorkFileFromPath });
    render(WorkDetail, { client: client as never, work: WORK });

    await openModal(/from server path/i);
    await fireEvent.input(screen.getByLabelText('Server file path'), {
      target: { value: '/shared/papers/paper.pdf' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Proceed' }));

    await waitFor(() =>
      expect(attachWorkFileFromPath).toHaveBeenCalledWith('w1', '/shared/papers/paper.pdf'),
    );
    expect(await screen.findByText(/Attached “paper\.pdf”/)).toBeTruthy();
    // The file list was refetched after the attach (initial load + refresh).
    expect((client.listWorkFiles as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(1);
  });

  it('path mode: a refusal (outside allowed roots) shows the backend detail', async () => {
    const attachWorkFileFromPath = vi
      .fn()
      .mockRejectedValue(new Error('Path is not inside an allowed server folder.'));
    const client = makeClient({ attachWorkFileFromPath });
    render(WorkDetail, { client: client as never, work: WORK });

    await openModal(/from server path/i);
    await fireEvent.input(screen.getByLabelText('Server file path'), {
      target: { value: '/etc/passwd' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Proceed' }));

    expect(await screen.findByText(/not inside an allowed server folder/)).toBeTruthy();
  });
});
