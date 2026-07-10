import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { StagingBatch } from '../api/client';
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

  it('marks a blocked (duplicate) item unchecked by default in preview', async () => {
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

    const checkbox = (await screen.findByLabelText('Create paper from dup.pdf')) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    expect(screen.getByText(/same DOI as an existing paper/)).toBeTruthy();
  });
});
