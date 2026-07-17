import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { StagingBatch } from '../api/client';
import { get } from 'svelte/store';

import { pendingIdentifierImport } from '../lib/selection';
import { currentUser } from '../lib/session';
import ImportPage from './ImportPage.svelte';

function stagingItem(overrides = {}) {
  return {
    id: 'it1',
    filename: 'a.pdf',
    sha256: 'x',
    status: 'extracted',
    error: null,
    parsed: { title: 'Paper A', authors: ['Smith, J.'], year: 2020, doi: null },
    duplicates: {},
    created_work_id: null,
    ...overrides,
  };
}

function readyBatch(overrides: Partial<StagingBatch> = {}): StagingBatch {
  return {
    id: 'batch1',
    mode: 'preview',
    status: 'ready',
    target_shelf_id: null,
    created_at: '2026-07-10T00:00:00Z',
    updated_at: '2026-07-10T00:00:00Z',
    items: [stagingItem()],
    extraction_queued: true,
    ...overrides,
  } as StagingBatch;
}

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    listSources: vi.fn().mockResolvedValue([]),
    listShelves: vi.fn().mockResolvedValue([]),
    listServerImportRoots: vi.fn().mockResolvedValue([]),
    uploadPdfsMulti: vi.fn(),
    getStagingBatch: vi.fn(),
    commitStagingBatch: vi.fn(),
    ...overrides,
  };
}

function selectFiles(count = 1): void {
  const input = screen.getByLabelText('PDF files') as HTMLInputElement;
  const files = Array.from({ length: count }, (_, i) =>
    new File([`pdf-${i}`], `p${i}.pdf`, { type: 'application/pdf' }),
  );
  fireEvent.change(input, { target: { files } });
}

describe('ImportPage multi-PDF import (batch10 #1)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u', username: 'ed', role: 'editor' } as never);
  });
  afterEach(() => vi.clearAllMocks());

  it('preview flow shows the extraction table and commits selected papers', async () => {
    const client = makeClient({
      uploadPdfsMulti: vi.fn().mockResolvedValue(readyBatch()),
      getStagingBatch: vi
        .fn()
        .mockResolvedValue(readyBatch({ status: 'committed', items: [stagingItem({ created_work_id: 'w1' })] })),
      commitStagingBatch: vi.fn().mockResolvedValue({
        batch_id: 'batch1',
        created: 1,
        skipped: 0,
        created_work_ids: ['w1'],
        warnings: [],
      }),
    });
    render(ImportPage, { client: client as never });

    selectFiles(1);
    await fireEvent.click(screen.getByRole('button', { name: 'Preview & choose' }));

    // Preview table renders the extracted title.
    await waitFor(() => expect(screen.getByText('Paper A')).toBeTruthy());
    expect(client.uploadPdfsMulti).toHaveBeenCalledWith(expect.any(Array), 'preview', null);

    await fireEvent.click(screen.getByRole('button', { name: 'Create selected papers' }));
    await waitFor(() =>
      expect(client.commitStagingBatch).toHaveBeenCalledWith('batch1', {
        decisions: [{ item_id: 'it1', action: 'accept' }],
      }),
    );
    await waitFor(async () =>
      expect((await screen.findAllByText(/Created 1 paper/)).length).toBeGreaterThan(0),
    );
  });

  it('direct mode auto-creates and summarises without a commit step', async () => {
    const client = makeClient({
      uploadPdfsMulti: vi.fn().mockResolvedValue(
        readyBatch({ status: 'committed', items: [stagingItem({ created_work_id: 'w1' })] }),
      ),
    });
    render(ImportPage, { client: client as never });

    selectFiles(1);
    await fireEvent.click(screen.getByRole('button', { name: 'Import directly' }));

    await waitFor(() =>
      expect(client.uploadPdfsMulti).toHaveBeenCalledWith(expect.any(Array), 'direct', null),
    );
    await waitFor(() => expect(screen.getByText(/Imported 1 paper/)).toBeTruthy());
    expect(client.commitStagingBatch).not.toHaveBeenCalled();
  });

  it('offers append-to-existing for a DOI collision, with create-new refused', async () => {
    const blocked = stagingItem({
      id: 'dup',
      filename: 'dup.pdf',
      duplicates: { same_doi: [{ work_id: 'w9', title: 'Existing' }] },
    });
    const client = makeClient({
      uploadPdfsMulti: vi.fn().mockResolvedValue(readyBatch({ items: [blocked] })),
    });
    render(ImportPage, { client: client as never });

    selectFiles(1);
    await fireEvent.click(screen.getByRole('button', { name: 'Preview & choose' }));

    // A colliding item renders the action select instead of a checkbox, defaulting to Skip.
    const select = (await screen.findByLabelText('Action for dup.pdf')) as HTMLSelectElement;
    expect(select.value).toBe('skip');
    const options = Array.from(select.options).map((o) => ({
      value: o.value,
      disabled: o.disabled,
    }));
    // Create-new is refused for a same-DOI collision; attach-to-existing is offered.
    expect(options.find((o) => o.value === 'accept')?.disabled).toBe(true);
    expect(options.some((o) => o.value === 'append:w9')).toBe(true);
    // The collision warning links the matching paper for in-Library verification.
    expect(screen.getByText(/same DOI as/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /“Existing”/ })).toBeTruthy();
  });

  it('allows create-new OR append for a title-only match', async () => {
    const titled = stagingItem({
      id: 'tm',
      filename: 'tm.pdf',
      duplicates: { same_title: [{ work_id: 'w7', title: 'Workshop version' }] },
    });
    const client = makeClient({
      uploadPdfsMulti: vi.fn().mockResolvedValue(readyBatch({ items: [titled] })),
    });
    render(ImportPage, { client: client as never });

    selectFiles(1);
    await fireEvent.click(screen.getByRole('button', { name: 'Preview & choose' }));

    const select = (await screen.findByLabelText('Action for tm.pdf')) as HTMLSelectElement;
    const options = Array.from(select.options);
    // Title matches don't block creating a new paper (workshop vs. journal version).
    expect(options.find((o) => o.value === 'accept')?.disabled).toBe(false);
    expect(options.some((o) => o.value === 'append:w7')).toBe(true);
  });

  it('commits an append decision with the chosen target paper', async () => {
    const blocked = stagingItem({
      id: 'dup2',
      filename: 'dup2.pdf',
      duplicates: { same_doi: [{ work_id: 'w9', title: 'Existing' }] },
    });
    const commitStagingBatch = vi.fn().mockResolvedValue({
      batch_id: 'batch1',
      created: 0,
      appended: 1,
      skipped: 0,
      created_work_ids: [],
      warnings: [],
    });
    const client = makeClient({
      uploadPdfsMulti: vi.fn().mockResolvedValue(readyBatch({ items: [blocked] })),
      commitStagingBatch,
      getStagingBatch: vi
        .fn()
        .mockResolvedValue(readyBatch({ status: 'committed', items: [blocked] })),
    });
    render(ImportPage, { client: client as never });

    selectFiles(1);
    await fireEvent.click(screen.getByRole('button', { name: 'Preview & choose' }));
    const select = (await screen.findByLabelText('Action for dup2.pdf')) as HTMLSelectElement;
    await fireEvent.change(select, { target: { value: 'append:w9' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Create selected papers' }));

    await waitFor(() =>
      expect(commitStagingBatch).toHaveBeenCalledWith('batch1', {
        decisions: [{ item_id: 'dup2', action: 'append', target_work_id: 'w9' }],
      }),
    );
    expect((await screen.findAllByText(/attached 1 PDF/)).length).toBeGreaterThan(0);
  });

  it('warns when two files in the batch parsed to the same DOI and lets one clear it', async () => {
    const book = stagingItem({
      id: 'bk',
      filename: 'book.pdf',
      parsed: { title: 'The Book', authors: [], year: 2020, doi: '10.1/shared' },
    });
    const chapter = stagingItem({
      id: 'ch',
      filename: 'chapter.pdf',
      parsed: { title: 'A Chapter', authors: [], year: 2020, doi: '10.1/shared' },
    });
    const cleared = { ...chapter, parsed: { ...chapter.parsed, doi: null } };
    const patchStagingItemDoi = vi.fn().mockResolvedValue(cleared);
    const client = makeClient({
      uploadPdfsMulti: vi.fn().mockResolvedValue(readyBatch({ items: [book, chapter] })),
      patchStagingItemDoi,
    });
    render(ImportPage, { client: client as never });

    selectFiles(2);
    await fireEvent.click(screen.getByRole('button', { name: 'Preview & choose' }));

    // Both rows carry the intra-batch warning before any edit.
    expect((await screen.findAllByText(/same DOI as .* in this batch/)).length).toBe(2);

    // Clear the chapter's DOI through the inline editor.
    await fireEvent.click(screen.getAllByRole('button', { name: 'edit' })[1]);
    await fireEvent.click(screen.getByRole('button', { name: 'Clear DOI' }));
    await waitFor(() => expect(patchStagingItemDoi).toHaveBeenCalledWith('batch1', 'ch', null));
    // The clash warnings are gone once only one file keeps the DOI.
    await waitFor(() => expect(screen.queryAllByText(/same DOI as .* in this batch/).length).toBe(0));
  });
});

describe('ImportPage sub-tabs', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u', username: 'ed', role: 'editor' } as never);
    sessionStorage.clear();
  });
  afterEach(() => vi.clearAllMocks());

  it('defaults to PDF import and switches panels per sub-tab', async () => {
    render(ImportPage, { client: makeClient() as never });

    // Default tab shows the PDF upload card only.
    expect(screen.getByLabelText('PDF files')).toBeTruthy();
    expect(screen.queryByLabelText('BibTeX')).toBeNull();

    await fireEvent.click(screen.getByRole('button', { name: 'Citations' }));
    expect(screen.queryByLabelText('PDF files')).toBeNull();
    expect(screen.getByLabelText('BibTeX')).toBeTruthy();
    expect(screen.getByLabelText('Citations, one per line')).toBeTruthy();

    await fireEvent.click(screen.getByRole('button', { name: 'External data' }));
    expect(screen.getByLabelText('RIS')).toBeTruthy();
    expect(screen.getByLabelText('CSL JSON')).toBeTruthy();
  });

  it('BibTeX preview & choose feeds the shared draft review and commits via batch commit', async () => {
    const client = makeClient({
      bibtexImportPreview: vi.fn().mockResolvedValue({
        drafts: [
          {
            line_index: 0,
            raw_line: '@article{vaswani2017}',
            engine: 'bibtex',
            suggested_title: 'Attention Is All You Need',
            suggested_authors: ['Ashish Vaswani'],
            suggested_year: 2017,
            suggested_doi: '10.5555/ATTN',
            suggested_venue: 'NeurIPS',
            suggested_abstract: null,
            match_status: 'matched',
            candidates: [],
            suggested_arxiv_id: '1706.03762',
            suggested_work_type: 'article',
            existing_work_id: null,
          },
        ],
        degraded: false,
        grobid_unavailable: false,
      }),
      batchImportCommit: vi.fn().mockResolvedValue({
        id: 'b1',
        source_id: null,
        input_type: 'batch_bibtex',
        status: 'completed',
        stats: { created: 1, matched: 0, skipped: 0 },
        created_at: '',
        started_at: null,
        finished_at: null,
      }),
    });
    render(ImportPage, { client: client as never });

    await fireEvent.click(screen.getByRole('button', { name: 'Citations' }));
    await fireEvent.input(screen.getByLabelText('BibTeX'), {
      target: { value: '@article{vaswani2017, title={Attention Is All You Need}}' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Preview & choose' }));

    const title = (await screen.findByLabelText('Title')) as HTMLInputElement;
    expect(title.value).toBe('Attention Is All You Need');

    await fireEvent.click(screen.getByRole('button', { name: 'Commit selected' }));
    await waitFor(() => expect(client.batchImportCommit).toHaveBeenCalled());
    const [drafts, options] = (client.batchImportCommit as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options).toMatchObject({ engine: 'bibtex' });
    expect(drafts[0]).toMatchObject({
      title: 'Attention Is All You Need',
      arxiv_id: '1706.03762',
      work_type: 'article',
    });
    // Fully committed → the paste box is cleared.
    expect((screen.getByLabelText('BibTeX') as HTMLTextAreaElement).value).toBe('');
  });

  it('Identifier preview & choose fetches metadata into the review table and commits', async () => {
    const client = makeClient({
      externalPreview: vi.fn().mockResolvedValue({
        available: true,
        title: 'Attention Is All You Need',
        authors: ['Ashish Vaswani'],
        year: 2017,
        venue: 'NeurIPS',
        abstract: null,
        doi: null,
        arxiv_id: '1706.03762',
        sources: ['arxiv'],
        message: null,
      }),
      batchImportCommit: vi.fn().mockResolvedValue({
        id: 'b1',
        source_id: null,
        input_type: 'batch_identifier',
        status: 'completed',
        stats: { created: 1, matched: 0, skipped: 0 },
        created_at: '',
        started_at: null,
        finished_at: null,
      }),
      importByIdentifier: vi.fn(),
    });
    render(ImportPage, { client: client as never });

    await fireEvent.click(screen.getByRole('button', { name: 'Identifier' }));
    await fireEvent.input(screen.getByLabelText('arXiv id or DOI'), {
      target: { value: '1706.03762' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Preview & choose' }));

    await waitFor(() =>
      expect(client.externalPreview).toHaveBeenCalledWith({ arxiv: '1706.03762' }),
    );
    const title = (await screen.findByLabelText('Title')) as HTMLInputElement;
    expect(title.value).toBe('Attention Is All You Need');

    await fireEvent.click(screen.getByRole('button', { name: 'Commit selected' }));
    await waitFor(() => expect(client.batchImportCommit).toHaveBeenCalled());
    const [drafts, options] = (client.batchImportCommit as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options).toMatchObject({ engine: 'identifier' });
    expect(drafts[0]).toMatchObject({
      title: 'Attention Is All You Need',
      arxiv_id: '1706.03762',
    });
    expect(client.importByIdentifier).not.toHaveBeenCalled();
    // Fully committed → the identifier box is cleared.
    expect((screen.getByLabelText('arXiv id or DOI') as HTMLInputElement).value).toBe('');
  });

  it('remembers the last selected sub-tab across remounts (session)', async () => {
    const first = render(ImportPage, { client: makeClient() as never });
    await fireEvent.click(screen.getByRole('button', { name: 'Identifier' }));
    expect(screen.getByLabelText('arXiv id or DOI')).toBeTruthy();
    first.unmount();

    render(ImportPage, { client: makeClient() as never });
    expect(screen.getByLabelText('arXiv id or DOI')).toBeTruthy();
    expect(screen.queryByLabelText('PDF files')).toBeNull();
  });
});

describe('ImportPage pending identifier import (Insights external-node click)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u', username: 'ed', role: 'editor' } as never);
    sessionStorage.clear();
  });
  afterEach(() => vi.clearAllMocks());

  it('opens the Identifier sub-tab with the pushed DOI prefilled', async () => {
    const client = makeClient();
    render(ImportPage, { client: client as never });

    pendingIdentifierImport.set('10.1177/0278364913481635');
    await waitFor(() => {
      const input = screen.getByLabelText('arXiv id or DOI') as HTMLInputElement;
      expect(input.value).toBe('10.1177/0278364913481635');
    });
    // Consumed once — the store resets so a later Import visit doesn't re-prefill.
    expect(get(pendingIdentifierImport)).toBeNull();
  });
});
