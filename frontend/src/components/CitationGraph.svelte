<script lang="ts">
  import { onDestroy } from 'svelte';

  import type { CitationGraphResponse, GraphNodeMode } from '../api/client';

  export let label = '';
  export let disabled = false;
  export let load: (
    nodeMode: GraphNodeMode,
    collapseVersions: boolean,
  ) => Promise<CitationGraphResponse> = async () => ({
    nodes: [],
    edges: [],
    summary: {},
  });
  // Called with a node's work_id when a local node is clicked (opens the work).
  export let onOpenWork: ((workId: string) => void) | null = null;
  // Whether the enclosing tab is visible (#9). Cytoscape mis-sizes when built while its container
  // is `display:none`, so when the tab becomes visible again we resize + re-run the layout.
  export let visible = true;

  let wasVisible = true;
  $: {
    if (visible && !wasVisible && cy) {
      cy.resize();
      relayout();
    }
    wasVisible = visible;
  }

  let nodeMode: GraphNodeMode = 'local_only';
  let collapseVersions = false;
  let renderMode: 'graph' | 'list' = 'graph';
  let layout = 'cose';
  let graph: CitationGraphResponse | null = null;
  let busy = false;

  let cyContainer: HTMLDivElement | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let cy: any = null;
  let cyError = false;

  async function build(): Promise<void> {
    busy = true;
    try {
      graph = await load(nodeMode, collapseVersions);
    } finally {
      busy = false;
    }
  }

  function nodeLabel(id: string): string {
    return graph?.nodes.find((node) => node.id === id)?.label ?? id;
  }

  function degrees(g: CitationGraphResponse): Record<string, number> {
    const deg: Record<string, number> = {};
    for (const edge of g.edges) {
      deg[edge.source] = (deg[edge.source] ?? 0) + edge.weight;
      deg[edge.target] = (deg[edge.target] ?? 0) + edge.weight;
    }
    return deg;
  }

  async function renderGraph(): Promise<void> {
    if (!graph || !cyContainer) return;
    try {
      // Dynamically imported and called untyped: the lazy chunk keeps cytoscape out of the
      // initial bundle, and per-layout options (e.g. `animate`) aren't in the base type.
      const cytoscape = (await import('cytoscape')).default as (options: unknown) => typeof cy;
      if (cy) {
        cy.destroy();
        cy = null;
      }
      const deg = degrees(graph);
      const maxDeg = Math.max(1, ...Object.values(deg));
      const elements = [
        ...graph.nodes.map((node) => ({
          data: {
            id: node.id,
            label: node.label,
            kind: node.type,
            workId: node.work_id,
            deg: deg[node.id] ?? 0,
          },
        })),
        ...graph.edges.map((edge) => ({
          data: {
            source: edge.source,
            target: edge.target,
            weight: edge.weight,
            resolution: edge.resolution,
          },
        })),
      ];
      cy = cytoscape({
        container: cyContainer,
        elements,
        style: [
          {
            selector: 'node',
            style: {
              label: 'data(label)',
              'font-size': 9,
              'text-wrap': 'ellipsis',
              'text-max-width': '120px',
              color: '#1f2a36',
              'background-color': '#3b6ea5',
              width: `mapData(deg, 0, ${maxDeg}, 16, 56)`,
              height: `mapData(deg, 0, ${maxDeg}, 16, 56)`,
            },
          },
          {
            selector: 'node[kind = "external"]',
            style: { 'background-color': '#b0bccb', shape: 'diamond' },
          },
          {
            selector: 'edge',
            style: {
              width: 'mapData(weight, 1, 5, 1, 5)',
              'line-color': '#bcc7d2',
              'target-arrow-color': '#bcc7d2',
              'target-arrow-shape': 'triangle',
              'curve-style': 'bezier',
            },
          },
        ],
        layout: { name: layout, animate: false },
      });
      cy.on('tap', 'node', (event: { target: { data: (k: string) => string } }) => {
        const workId = event.target.data('workId');
        if (workId && onOpenWork) onOpenWork(workId);
      });
      cyError = false;
    } catch {
      // Cytoscape needs a canvas-capable DOM; fall back to the list renderer.
      cyError = true;
    }
  }

  function relayout(): void {
    if (cy) cy.layout({ name: layout, animate: false }).run();
  }

  $: if (renderMode === 'graph' && graph && cyContainer) void renderGraph();

  onDestroy(() => {
    if (cy) cy.destroy();
  });
</script>

<section>
  <div class="head">
    <h3>Citation graph {label}</h3>
    <div class="controls">
      <div class="seg" role="group" aria-label="Render mode">
        <button
          type="button"
          class:active={renderMode === 'graph'}
          on:click={() => (renderMode = 'graph')}
          title="Show an interactive node-link graph"
        >
          Graph
        </button>
        <button
          type="button"
          class:active={renderMode === 'list'}
          on:click={() => (renderMode = 'list')}
          title="Show the citation edges as a plain list"
        >
          List
        </button>
      </div>
      {#if renderMode === 'graph'}
        <select bind:value={layout} on:change={relayout} disabled={disabled || busy}
          title="Graph layout algorithm">
          <option value="cose">Force</option>
          <option value="circle">Circle</option>
          <option value="grid">Grid</option>
          <option value="breadthfirst">Hierarchy</option>
        </select>
      {/if}
      <select bind:value={nodeMode} disabled={disabled || busy}
        title="Whether to include papers outside the library as external nodes">
        <option value="local_only">Local only</option>
        <option value="include_external">Include external</option>
      </select>
      <label class="toggle" title="Merge papers linked as versions of one another into one node">
        <input
          type="checkbox"
          bind:checked={collapseVersions}
          disabled={disabled || busy}
          aria-label="Collapse works linked as versions into one node"
        />
        Collapse versions
      </label>
      <button type="button" on:click={build} disabled={disabled || busy}
        title="Build the citation graph for the chosen scope">Build graph</button>
    </div>
  </div>

  {#if graph}
    <p class="summary">
      {graph.summary.node_count ?? graph.nodes.length} nodes · {graph.summary.edge_count ??
        graph.edges.length} edges · {graph.summary.external_node_count ?? 0} external ·
      {graph.summary.unresolved_reference_count ?? 0} unresolved
    </p>

    {#if graph.edges.length === 0 && graph.nodes.length === 0}
      <p class="empty">No citation edges in this scope yet.</p>
    {:else if renderMode === 'graph'}
      <div class="cy" bind:this={cyContainer}></div>
      {#if cyError}
        <p class="empty">Interactive view unavailable here — switch to List.</p>
      {:else}
        <p class="hint">Node size ≈ citation degree · click a node to open the work.</p>
      {/if}
    {:else}
      <ul class="edges">
        {#each graph.edges as edge (edge.source + '->' + edge.target)}
          <li>
            <span>{nodeLabel(edge.source)}</span>
            <span class="arrow">→</span>
            <span>{nodeLabel(edge.target)}</span>
            <small>{edge.resolution}{edge.weight > 1 ? ` ·×${edge.weight}` : ''}</small>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</section>

<style>
  .head {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    justify-content: space-between;
  }

  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .seg {
    display: flex;
  }

  .toggle {
    align-items: center;
    color: #21303d;
    display: flex;
    font-size: 0.85rem;
    font-weight: 700;
    gap: 0.35rem;
  }

  .seg button {
    border-radius: 0;
  }

  .seg button:first-child {
    border-radius: 6px 0 0 6px;
  }

  .seg button:last-child {
    border-left: none;
    border-radius: 0 6px 6px 0;
  }

  button {
    background: white;
    border: 1px solid #bcc7d2;
    border-radius: 6px;
    color: #21303d;
    cursor: pointer;
    font: inherit;
    font-weight: 700;
    min-height: 2rem;
    padding: 0.3rem 0.6rem;
  }

  button.active {
    background: #203142;
    color: white;
  }

  select {
    border: 1px solid #bcc7d2;
    border-radius: 6px;
    font: inherit;
    padding: 0.3rem 0.5rem;
  }

  .cy {
    background: #fbfcfd;
    border: 1px solid #d8dee6;
    border-radius: 6px;
    height: min(60vh, 34rem);
    width: 100%;
  }

  .summary,
  .hint {
    color: #64717f;
    font-size: 0.85rem;
  }

  .hint {
    margin: 0.3rem 0 0;
  }

  .edges {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .edges li {
    align-items: baseline;
    display: flex;
    gap: 0.4rem;
  }

  .arrow,
  small {
    color: #64717f;
  }
</style>
