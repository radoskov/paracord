import { render, screen } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Summary, Work } from '../api/client';
import { currentUser } from '../lib/session';
import WorkDetail from './WorkDetail.svelte';

function makeWork(overrides: Partial<Work> = {}): Work {
  return {
    id: 'w1',
    canonical_title: 'Attention Is All You Need',
    abstract: 'transformers and attention',
    doi: null,
    arxiv_id: null,
    venue: null,
    year: 2017,
    reading_status: 'unread',
    canonical_metadata_source: null,
    confirmed_fields: [],
    keywords: [],
    topics: [],
    created_by_user_id: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  } as unknown as Work;
}

function makeSummary(overrides: Partial<Summary> = {}): Summary {
  return {
    id: 's1',
    entity_type: 'work',
    entity_id: 'w1',
    summary_type: 'local_llm',
    text: 'A short summary.',
    model_name: 'tier1-extractive-frequency',
    prompt_version: 'local-llm-v1',
    created_at: '2024-01-01T00:00:00Z',
    ...overrides,
  } as unknown as Summary;
}

function makeClient(summaries: Summary[]) {
  return {
    listWorkMetadata: vi.fn().mockResolvedValue([]),
    listWorkFiles: vi.fn().mockResolvedValue([]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue(summaries),
    listTags: vi.fn().mockResolvedValue([]),
    listWorkTags: vi.fn().mockResolvedValue([]),
  };
}

describe('WorkDetail summary provider-fallback indicator (Phase B2)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
  });

  it('shows the extractive-fallback hint when the summary degraded', async () => {
    const client = makeClient([makeSummary({ fallback: true, provider_used: 'extractive' })]);
    render(WorkDetail, { client: client as never, work: makeWork() });
    expect(
      await screen.findByText('Summarized with the extractive fallback (LLM unavailable).'),
    ).toBeTruthy();
  });

  it('stays quiet when the summary did not degrade', async () => {
    const client = makeClient([makeSummary({ fallback: false, summary_type: 'extractive' })]);
    render(WorkDetail, { client: client as never, work: makeWork() });
    // Let the async load settle so the summaries block has rendered.
    await screen.findByText('extractive: A short summary.');
    expect(
      screen.queryByText('Summarized with the extractive fallback (LLM unavailable).'),
    ).toBeNull();
  });
});
