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

const SAVED_FILTER = {
  id: 'sf-1',
  name: 'Recent unread',
  search_mode: 'metadata' as const,
  query_text: 'transformer',
  params: { reading_status: 'unread', missing: [] },
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
};

function makeClient() {
  return {
    listShelves: vi.fn().mockResolvedValue([]),
    listRacks: vi.fn().mockResolvedValue([]),
    listImportBatches: vi.fn().mockResolvedValue([BATCH]),
    listSavedFilters: vi.fn().mockResolvedValue([SAVED_FILTER]),
    listWorks: vi.fn().mockResolvedValue({
      items: [{ id: 'w1' }, { id: 'w2' }],
      total: 2,
      page: 1,
      pages: 1,
      per_page: 500,
    }),
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

    await waitFor(() =>
      expect(client.listWorks).toHaveBeenCalledWith({ q: 'attention', perPage: 500 }),
    );
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

  it('saved_filter: shows the saved-filter picker and sends the filter id as scopeId', async () => {
    const client = makeClient();
    render(InsightsPage, { client: client as never });
    await waitFor(() => expect(client.listSavedFilters).toHaveBeenCalled());

    await selectScope('saved_filter');
    const select = screen.getByLabelText('Saved filter') as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toContain('Recent unread');
    await fireEvent.change(select, { target: { value: 'sf-1' } });
    await build();

    expect(client.citationGraph).toHaveBeenCalledWith({
      scopeType: 'saved_filter',
      scopeId: 'sf-1',
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
