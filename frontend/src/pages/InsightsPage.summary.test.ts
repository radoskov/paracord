import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { ScopeSummaryResponse } from '../api/client';
import InsightsPage from './InsightsPage.svelte';

const EXTRACTIVE_HINT =
  'Extractive summary — set an AI summary model in Admin → AI to enable model-based summaries.';

function makeClient(summary: ScopeSummaryResponse) {
  return {
    listShelves: vi.fn().mockResolvedValue([]),
    listRacks: vi.fn().mockResolvedValue([]),
    listImportBatches: vi.fn().mockResolvedValue([]),
    listSavedFilters: vi.fn().mockResolvedValue([]),
    citationGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    createScopeScope: vi.fn().mockResolvedValue(summary),
  };
}

function baseSummary(over: Partial<ScopeSummaryResponse>): ScopeSummaryResponse {
  return {
    entity_type: 'library',
    entity_id: 'lib',
    summary_type: 'extractive',
    text: 'A summary of the scope.',
    model_name: 'tier1-extractive-frequency-scope',
    prompt_version: 'v1',
    work_count: 3,
    provider_requested: 'extractive',
    provider_used: 'extractive',
    fallback: false,
    fallback_reason: null,
    ...over,
  };
}

async function summarize(): Promise<void> {
  await fireEvent.click(screen.getByRole('button', { name: /summarize/i }));
}

describe('InsightsPage scope summary (L4) + no search (L2)', () => {
  it('shows the extractive hint when the summary is the extractive fallback (no model)', async () => {
    const client = makeClient(baseSummary({}));
    render(InsightsPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await summarize();
    await waitFor(() => expect(screen.getByText(EXTRACTIVE_HINT)).toBeTruthy());
  });

  it('shows a reason when the configured model was unavailable and it fell back', async () => {
    const client = makeClient(
      baseSummary({ fallback: true, fallback_reason: 'the local LLM is unavailable' }),
    );
    render(InsightsPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await summarize();
    await waitFor(() =>
      expect(screen.getByText(/the local LLM is unavailable/)).toBeTruthy(),
    );
    expect(screen.queryByText(EXTRACTIVE_HINT)).toBeNull();
  });

  it('shows no extractive hint when a model-based (local_llm) summary was produced', async () => {
    const client = makeClient(
      baseSummary({ summary_type: 'local_llm', provider_used: 'local_llm', model_name: 'llama3' }),
    );
    render(InsightsPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await summarize();
    await waitFor(() => expect(screen.getByText('A summary of the scope.')).toBeTruthy());
    expect(screen.queryByText(EXTRACTIVE_HINT)).toBeNull();
  });

  it('no longer renders the redundant Insights-tab search (L2)', async () => {
    const client = makeClient(baseSummary({}));
    render(InsightsPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    expect(screen.queryByLabelText('Search query')).toBeNull();
    expect(screen.queryByLabelText('Search mode')).toBeNull();
  });
});
