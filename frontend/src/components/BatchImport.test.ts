import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { BatchPreviewResponse, ImportBatch } from '../api/client';
import BatchImport from './BatchImport.svelte';

function previewResponse(): BatchPreviewResponse {
  return {
    drafts: [
      {
        line_index: 0,
        raw_line: 'Attention Is All You Need',
        engine: 'lookup',
        suggested_title: 'Attention Is All You Need',
        suggested_authors: ['Ashish Vaswani'],
        suggested_year: 2017,
        suggested_doi: '10.5555/ATTN',
        suggested_venue: 'NeurIPS',
        suggested_abstract: null,
        match_status: 'matched',
        candidates: [
          {
            title: 'Attention Is All You Need',
            authors: ['Ashish Vaswani'],
            year: 2017,
            doi: '10.5555/ATTN',
            venue: null,
            source: 'crossref',
            sources: ['crossref'],
            confidence: 0.92,
          },
        ],
      },
    ],
    degraded: false,
    grobid_unavailable: false,
  };
}

function mockClient() {
  const batch: ImportBatch = {
    id: 'b1',
    source_id: null,
    input_type: 'batch_lookup',
    status: 'completed',
    stats: { created: 1, matched: 0, skipped: 0 },
    created_at: '',
    started_at: null,
    finished_at: null,
  };
  return {
    batchImportPreview: vi.fn().mockResolvedValue(previewResponse()),
    batchImportCommit: vi.fn().mockResolvedValue(batch),
    listShelves: vi.fn().mockResolvedValue([]),
  };
}

describe('BatchImport', () => {
  it('previews a staging row, then edits + unchecks + commits the right payload', async () => {
    const client = mockClient();
    render(BatchImport, { client: client as never });

    // Paste two lines and preview.
    const textarea = screen.getByLabelText(/Citations, one per line/i);
    await fireEvent.input(textarea, { target: { value: 'Attention Is All You Need\n  ' } });
    await fireEvent.click(screen.getByRole('button', { name: /^Preview$/i }));

    // The lookup ran with the trimmed, non-empty lines only.
    expect(client.batchImportPreview).toHaveBeenCalledWith(
      ['Attention Is All You Need'],
      'lookup',
    );

    // A staging row renders with the suggested title and a "matched" badge.
    const titleInput = (await screen.findByLabelText('Title')) as HTMLInputElement;
    expect(titleInput.value).toBe('Attention Is All You Need');
    expect(screen.getByText(/matched/i)).toBeTruthy();

    // Edit the title.
    await fireEvent.input(titleInput, { target: { value: 'Edited Title' } });

    // Commit selected (the row is included by default for a matched draft).
    await fireEvent.click(screen.getByRole('button', { name: /Commit selected/i }));

    await waitFor(() => expect(client.batchImportCommit).toHaveBeenCalled());
    const [drafts, options] = client.batchImportCommit.mock.calls[0];
    expect(drafts).toHaveLength(1);
    expect(drafts[0]).toMatchObject({
      title: 'Edited Title',
      authors: ['Ashish Vaswani'],
      year: 2017,
      doi: '10.5555/ATTN',
      include: true,
    });
    expect(options).toMatchObject({ engine: 'lookup', enrich: true });

    // The committed stats are surfaced.
    expect(await screen.findByText(/1 created/i)).toBeTruthy();
  });

  it('excluding a row keeps it out of the commit payload', async () => {
    const client = mockClient();
    render(BatchImport, { client: client as never });

    const textarea = screen.getByLabelText(/Citations, one per line/i);
    await fireEvent.input(textarea, { target: { value: 'Attention Is All You Need' } });
    await fireEvent.click(screen.getByRole('button', { name: /^Preview$/i }));

    // Uncheck the only row, then commit -> guarded, nothing sent.
    const include = (await screen.findByLabelText('Include this paper')) as HTMLInputElement;
    await fireEvent.click(include);
    await fireEvent.click(screen.getByRole('button', { name: /Commit selected/i }));

    expect(client.batchImportCommit).not.toHaveBeenCalled();
    expect(screen.getByText(/Select at least one paper/i)).toBeTruthy();
  });
});
