import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ImportBatch } from '../api/client';
import { selectedPaperIds } from '../lib/selection';
import InsightsPage from './InsightsPage.svelte';

const EMPTY_GRAPH = { nodes: [], edges: [], summary: {} };

const BATCH: ImportBatch = {
  id: 'batch-1',
  source_id: null,
  created_by_user_id: 'u1',
  input_type: 'bibtex',
  status: 'completed',
  stats: null,
  created_at: '2026-06-01T00:00:00Z',
  started_at: null,
  finished_at: null,
  work_count: 3,
};

function makeClient() {
  return {
    listShelves: vi.fn().mockResolvedValue([]),
    listRacks: vi.fn().mockResolvedValue([]),
    listImportBatches: vi.fn().mockResolvedValue([BATCH]),
    listWorks: vi.fn().mockResolvedValue([{ id: 'w1' }, { id: 'w2' }]),
    citationGraph: vi.fn().mockResolvedValue(EMPTY_GRAPH),
    semanticSearch: vi.fn(),
  };
}

async function selectScope(value: string): Promise<void> {
  const select = screen.getByLabelText('Scope type') as HTMLSelectElement;
  await fireEvent.change(select, { target: { value } });
}

async function build(): Promise<void> {
  await fireEvent.click(screen.getByRole('button', { name: /build graph/i }));
}

describe('InsightsPage Phase B6 graph scopes', () => {
  beforeEach(() => selectedPaperIds.set([]));
  afterEach(() => selectedPaperIds.set([]));

  it('search_result: runs the search then sends the resulting ids', async () => {
    const client = makeClient();
    render(InsightsPage, { client: client as never });
    await waitFor(() => expect(client.listImportBatches).toHaveBeenCalled());

    await selectScope('search_result');
    const input = screen.getByLabelText('Graph search query') as HTMLInputElement;
    await fireEvent.input(input, { target: { value: 'attention' } });
    await build();

    await waitFor(() => expect(client.listWorks).toHaveBeenCalledWith({ q: 'attention' }));
    expect(client.citationGraph).toHaveBeenCalledWith({
      scopeType: 'search_result',
      workIds: ['w1', 'w2'],
      nodeMode: 'local_only',
      collapseVersions: false,
    });
  });

  it('selected_papers: shows the count and sends the current selection', async () => {
    selectedPaperIds.set(['s1', 's2', 's3']);
    const client = makeClient();
    render(InsightsPage, { client: client as never });
    await waitFor(() => expect(client.listImportBatches).toHaveBeenCalled());

    await selectScope('selected_papers');
    expect(screen.getByText('3 papers selected')).toBeTruthy();
    await build();

    expect(client.citationGraph).toHaveBeenCalledWith({
      scopeType: 'selected_papers',
      workIds: ['s1', 's2', 's3'],
      nodeMode: 'local_only',
      collapseVersions: false,
    });
  });

  it('import_batch: shows the batch picker and sends the batch id', async () => {
    const client = makeClient();
    render(InsightsPage, { client: client as never });
    await waitFor(() => expect(client.listImportBatches).toHaveBeenCalled());

    await selectScope('import_batch');
    const select = screen.getByLabelText('Import batch') as HTMLSelectElement;
    await fireEvent.change(select, { target: { value: 'batch-1' } });
    await build();

    expect(client.citationGraph).toHaveBeenCalledWith({
      scopeType: 'import_batch',
      scopeId: 'batch-1',
      nodeMode: 'local_only',
      collapseVersions: false,
    });
  });

  it('propagates the collapse-versions toggle into the payload', async () => {
    selectedPaperIds.set(['s1']);
    const client = makeClient();
    render(InsightsPage, { client: client as never });
    await waitFor(() => expect(client.listImportBatches).toHaveBeenCalled());

    await selectScope('selected_papers');
    await fireEvent.click(screen.getByLabelText(/collapse works linked as versions/i));
    await build();

    expect(client.citationGraph).toHaveBeenCalledWith(
      expect.objectContaining({ collapseVersions: true }),
    );
  });
});
