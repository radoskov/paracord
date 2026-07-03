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
  it('builds on demand and renders edges with resolved labels in list mode', async () => {
    const load = vi.fn(async () => GRAPH);
    render(CitationGraph, { label: '· whole library', load });

    // Nothing fetched until the user asks for it.
    expect(load).not.toHaveBeenCalled();

    await fireEvent.click(screen.getByRole('button', { name: /build graph/i }));

    expect(load).toHaveBeenCalledWith('local_only', false, 'none');
    // Summary renders regardless of render mode.
    expect(screen.getByText(/2 nodes/)).toBeTruthy();

    // The interactive canvas can't render in jsdom; switch to the list renderer.
    await fireEvent.click(screen.getByRole('button', { name: /^list$/i }));
    expect(screen.getByText('Citing Paper')).toBeTruthy();
    expect(screen.getByText('Cited Paper')).toBeTruthy();
  });

  it('passes the version-collapse toggle through to load', async () => {
    const load = vi.fn(async () => GRAPH);
    render(CitationGraph, { label: '· whole library', load });

    const toggle = screen.getByLabelText(/collapse works linked as versions/i);
    await fireEvent.click(toggle);
    await fireEvent.click(screen.getByRole('button', { name: /build graph/i }));

    expect(load).toHaveBeenCalledWith('local_only', true, 'none');
  });

  it('refetches with the chosen color_by once a graph is built (§8.9 depth)', async () => {
    const load = vi.fn(async () => GRAPH);
    render(CitationGraph, { label: '· whole library', load });

    // First build fetches with the default (uncolored) grouping.
    await fireEvent.click(screen.getByRole('button', { name: /build graph/i }));
    expect(load).toHaveBeenLastCalledWith('local_only', false, 'none');

    // Changing color_by after a graph exists refetches to get the server-computed groups.
    await fireEvent.change(screen.getByTestId('graph-color-by'), { target: { value: 'status' } });
    expect(load).toHaveBeenLastCalledWith('local_only', false, 'status');
  });
});
