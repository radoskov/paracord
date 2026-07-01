import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { HybridSearchResponse } from '../api/client';
import InsightsPage from './InsightsPage.svelte';

const HINT =
  'Semantic ranking is using the built-in baseline embedder (sentence-transformers / Ollama not configured).';

function makeClient(response: HybridSearchResponse) {
  return {
    listShelves: vi.fn().mockResolvedValue([]),
    listRacks: vi.fn().mockResolvedValue([]),
    listImportBatches: vi.fn().mockResolvedValue([]),
    listSavedFilters: vi.fn().mockResolvedValue([]),
    citationGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    warmSearch: vi.fn().mockResolvedValue({ lexical_indexed_docs: 0, status: 'ok' }),
    search: vi.fn().mockResolvedValue(response),
  };
}

async function search(): Promise<void> {
  const input = screen.getByLabelText('Search query') as HTMLInputElement;
  await fireEvent.input(input, { target: { value: 'attention' } });
  await fireEvent.submit(input.closest('form') as HTMLFormElement);
}

describe('InsightsPage unified search (HS5)', () => {
  it('shows the degraded hint when the ranking fell back to the baseline embedder', async () => {
    const client = makeClient({
      query: 'attention',
      mode: 'hybrid',
      items: [{ work_id: 'w1', title: 'A paper', year: 2020, score: 0.8 }],
      embedding_provider_used: 'hash-bow-v1',
      embedding_provider_requested: 'sentence_transformers',
      degraded: true,
    });
    render(InsightsPage, { client: client as never });
    await search();
    await waitFor(() => expect(screen.getByText(HINT)).toBeTruthy());
  });

  it('stays quiet when the requested provider is active, and defaults to hybrid mode', async () => {
    const client = makeClient({
      query: 'attention',
      mode: 'hybrid',
      items: [{ work_id: 'w1', title: 'A paper', year: 2020, score: 0.8 }],
      embedding_provider_used: 'hash-bow-v1',
      embedding_provider_requested: 'hash_bow',
      degraded: false,
    });
    render(InsightsPage, { client: client as never });
    await search();
    await waitFor(() => expect(screen.getByText('A paper', { exact: false })).toBeTruthy());
    expect(screen.queryByText(HINT)).toBeNull();
    // Default mode is hybrid.
    expect(client.search).toHaveBeenCalledWith('attention', 'hybrid', 10);
  });

  it('shows the matching passage and a "both" badge for a hybrid hit found by both engines', async () => {
    const client = makeClient({
      query: 'attention',
      mode: 'hybrid',
      items: [
        {
          work_id: 'w1',
          title: 'A paper',
          year: 2020,
          score: 0.03,
          passage: 'the attention mechanism aggregates context',
          section: 'Methods',
          lexical_rank: 1,
          semantic_rank: 2,
        },
      ],
      embedding_provider_used: 'hash-bow-v1',
      degraded: false,
    });
    render(InsightsPage, { client: client as never });
    await search();
    await waitFor(() => expect(screen.getByText('both')).toBeTruthy());
    expect(screen.getByText(/attention mechanism aggregates context/)).toBeTruthy();
  });
});
