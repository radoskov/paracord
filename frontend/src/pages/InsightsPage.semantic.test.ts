import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { SemanticSearchResponse } from '../api/client';
import InsightsPage from './InsightsPage.svelte';

const HINT = 'Semantic search is using the built-in baseline (sentence-transformers not configured).';

function makeClient(response: SemanticSearchResponse) {
  return {
    listShelves: vi.fn().mockResolvedValue([]),
    listRacks: vi.fn().mockResolvedValue([]),
    citationGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    semanticSearch: vi.fn().mockResolvedValue(response),
  };
}

async function search(): Promise<void> {
  const input = screen.getByLabelText('Semantic query') as HTMLInputElement;
  await fireEvent.input(input, { target: { value: 'attention' } });
  // Submit the form (a click on the submit button doesn't reliably fire submit in jsdom).
  await fireEvent.submit(input.closest('form') as HTMLFormElement);
}

describe('InsightsPage semantic-search provider-fallback indicator (Phase B2)', () => {
  it('shows the degraded hint when the search fell back to the baseline embedder', async () => {
    const client = makeClient({
      query: 'attention',
      items: [{ work_id: 'w1', title: 'A paper', year: 2020, score: 0.8 }],
      embedding_provider_used: 'hash-bow-v1',
      embedding_provider_requested: 'sentence_transformers',
      degraded: true,
    });
    render(InsightsPage, { client: client as never });
    await search();
    await waitFor(() => expect(screen.getByText(HINT)).toBeTruthy());
  });

  it('stays quiet when the requested provider is active', async () => {
    const client = makeClient({
      query: 'attention',
      items: [{ work_id: 'w1', title: 'A paper', year: 2020, score: 0.8 }],
      embedding_provider_used: 'hash-bow-v1',
      embedding_provider_requested: 'hash_bow',
      degraded: false,
    });
    render(InsightsPage, { client: client as never });
    await search();
    await waitFor(() => expect(screen.getByText('A paper', { exact: false })).toBeTruthy());
    expect(screen.queryByText(HINT)).toBeNull();
  });
});
