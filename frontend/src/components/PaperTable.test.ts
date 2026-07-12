import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import type { Work } from '../api/client';
import { LIBRARY_COLUMNS } from '../lib/columns';
import PaperTable from './PaperTable.svelte';

function makeWork(overrides: Partial<Work> = {}): Work {
  return {
    id: 'w1',
    canonical_title: 'Attention Is All You Need',
    reading_status: 'unread',
    created_by_user_id: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  } as unknown as Work;
}

const NEW_COLUMNS = LIBRARY_COLUMNS.filter((c) =>
  ['title', 'file_count', 'topics', 'badges', 'tags'].includes(c.id),
);

describe('PaperTable batch10 columns (issue 5)', () => {
  it('renders file_count, topics, tags, and badge labels', () => {
    const work = makeWork({
      file_count: 3,
      topics: ['transformers', 'attention'],
      tags: [{ id: 't1', name: 'ml', color: '#f00' }],
      badges: ['extracted', 'conflicts', 'text_poor'],
    });
    render(PaperTable, { works: [work], columns: NEW_COLUMNS } as never);

    expect(screen.getByText('3')).toBeTruthy();
    expect(screen.getByText('transformers')).toBeTruthy();
    expect(screen.getByText('ml')).toBeTruthy();
    // Badge tokens are mapped to friendly labels.
    expect(screen.getByText('extracted')).toBeTruthy();
    expect(screen.getByText('conflicts')).toBeTruthy();
    expect(screen.getByText('poor text')).toBeTruthy();
  });

  it('shows placeholders when the enrichment fields are empty', () => {
    render(PaperTable, { works: [makeWork()], columns: NEW_COLUMNS } as never);
    // file_count falls back to 0; topics/tags/badges render a dash.
    expect(screen.getByText('0')).toBeTruthy();
    expect(screen.getAllByText('-').length).toBeGreaterThanOrEqual(3);
  });

  it('flags a per-paper processing error on the title (F2)', () => {
    const work = makeWork({ processing_error: 'enrich: DOI conflict' });
    render(PaperTable, { works: [work], columns: NEW_COLUMNS } as never);
    expect(screen.getByText(/processing failed/)).toBeTruthy();
  });
});
