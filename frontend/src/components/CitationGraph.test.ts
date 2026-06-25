import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { CitationGraphResponse } from '../api/client';
import CitationGraph from './CitationGraph.svelte';

const GRAPH: CitationGraphResponse = {
  nodes: [
    { id: 'a', label: 'Citing Paper', type: 'local', work_id: 'a', year: 2020, doi: null },
    { id: 'b', label: 'Cited Paper', type: 'local', work_id: 'b', year: 2019, doi: null },
  ],
  edges: [{ source: 'a', target: 'b', weight: 2, resolution: 'local_match' }],
  summary: { node_count: 2, edge_count: 1, external_node_count: 0, unresolved_reference_count: 0 },
};

describe('CitationGraph', () => {
  it('builds the graph on demand and renders edges with resolved labels', async () => {
    const load = vi.fn(async () => GRAPH);
    render(CitationGraph, { label: '· whole library', load });

    // Nothing fetched until the user asks for it.
    expect(load).not.toHaveBeenCalled();

    await fireEvent.click(screen.getByRole('button', { name: /build graph/i }));

    expect(load).toHaveBeenCalledWith('local_only');
    // Edge is rendered using node labels, not raw ids.
    expect(screen.getByText('Citing Paper')).toBeTruthy();
    expect(screen.getByText('Cited Paper')).toBeTruthy();
    expect(screen.getByText(/2 nodes/)).toBeTruthy();
  });
});
