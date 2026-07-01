<script lang="ts">
  import { onDestroy } from 'svelte';

  import type { CitationGraphResponse, GraphNodeMode, TopicGraphResponse } from '../api/client';

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
  // Topic (embedding-similarity) graph loader (#6). When provided, a Citation/Topic mode toggle is
  // shown; the topic graph shares the same Cytoscape rendering.
  export let loadTopic: (() => Promise<TopicGraphResponse>) | null = null;
  // Called with a node's work_id when a local node is clicked (opens the work).
  export let onOpenWork: ((workId: string) => void) | null = null;
  // Called with a doi/arxiv identifier when an external node's "import" action fires (#8).
  export let onImportExternal: ((doi: string) => void) | null = null;
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

  // Graph type: citation (default) or topic (embedding similarity, #6).
  let graphType: 'citation' | 'topic' = 'citation';
  let nodeMode: GraphNodeMode = 'local_only';
  let collapseVersions = false;
  let renderMode: 'graph' | 'list' = 'graph';
  // fcose (#8) is the preferred force layout; if the extension didn't load we fall back to cose.
  let layout = 'fcose';
  let graph: CitationGraphResponse | null = null;
  let topicGraph: TopicGraphResponse | null = null;
  let busy = false;
  // Hide nodes with no edges (#7); default ON.
  let hideSingletons = true;
  // Collapse a paper's external citation leaves beyond this many into a single "+K external" node (#8).
  const EXTERNAL_LEAF_CAP = 8;
  let hideExternalLeaves = false;

  let cyContainer: HTMLDivElement | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let cy: any = null;
  let cyError = false;
  let fcoseRegistered = false;
  // Hover tooltip (#8).
  let tooltip = { show: false, x: 0, y: 0, html: '' };

  async function build(): Promise<void> {
    busy = true;
    try {
      if (graphType === 'topic' && loadTopic) {
        topicGraph = await loadTopic();
        graph = null;
      } else {
        graph = await load(nodeMode, collapseVersions);
        topicGraph = null;
      }
    } finally {
      busy = false;
    }
  }

  // Unified node/edge shape for rendering, derived from whichever graph is active.
  type RNode = { id: string; label: string; kind: 'local' | 'external'; workId: string | null; year: number | null; venue: string | null; doi: string | null };
  type REdge = { source: string; target: string; weight: number; resolution?: string };

  $: rNodes = (() => {
    if (graphType === 'topic' && topicGraph) {
      return topicGraph.nodes.map<RNode>((n) => ({ id: n.id, label: n.label, kind: 'local', workId: n.work_id, year: n.year, venue: n.venue ?? null, doi: n.doi ?? null }));
    }
    if (graph) {
      return graph.nodes.map<RNode>((n) => ({ id: n.id, label: n.label, kind: n.type, workId: n.work_id, year: n.year, venue: n.venue ?? null, doi: n.doi }));
    }
    return [] as RNode[];
  })();
  $: rEdges = (() => {
    if (graphType === 'topic' && topicGraph) return topicGraph.edges.map<REdge>((e) => ({ source: e.source, target: e.target, weight: e.weight }));
    if (graph) return graph.edges.map<REdge>((e) => ({ source: e.source, target: e.target, weight: e.weight, resolution: e.resolution }));
    return [] as REdge[];
  })();

  // Apply the client-side filters (#7 hide singletons, #8 collapse external leaves) to produce the
  // node/edge set actually rendered.
  function filteredElements(): { nodes: RNode[]; edges: REdge[] } {
    let nodes = rNodes;
    let edges = rEdges;

    if (hideExternalLeaves) {
      // Drop external nodes and any edge touching them.
      const localIds = new Set(nodes.filter((n) => n.kind === 'local').map((n) => n.id));
      nodes = nodes.filter((n) => n.kind === 'local');
      edges = edges.filter((e) => localIds.has(e.source) && localIds.has(e.target));
    }

    if (hideSingletons) {
      const touched = new Set<string>();
      for (const e of edges) {
        touched.add(e.source);
        touched.add(e.target);
      }
      nodes = nodes.filter((n) => touched.has(n.id));
    }
    return { nodes, edges };
  }

  $: hasGraph = graphType === 'topic' ? topicGraph != null : graph != null;
  $: activeSummary =
    graphType === 'topic'
      ? topicGraph?.summary ?? null
      : (graph?.summary as Record<string, number> | null) ?? null;

  function nodeLabel(id: string): string {
    return rNodes.find((node) => node.id === id)?.label ?? id;
  }

  function degrees(edges: REdge[]): Record<string, number> {
    const deg: Record<string, number> = {};
    for (const edge of edges) {
      deg[edge.source] = (deg[edge.source] ?? 0) + edge.weight;
      deg[edge.target] = (deg[edge.target] ?? 0) + edge.weight;
    }
    return deg;
  }

  async function renderGraph(): Promise<void> {
    if (!hasGraph || !cyContainer) return;
    try {
      // Dynamically imported and called untyped: the lazy chunk keeps cytoscape out of the
      // initial bundle, and per-layout options (e.g. `animate`) aren't in the base type.
      const cytoscape = (await import('cytoscape')).default as (options: unknown) => typeof cy;
      // fcose (#8) is a nicer force layout than the built-in cose. Register once; if it can't load
      // (e.g. offline), fall back to cose so the graph still renders.
      if (!fcoseRegistered) {
        try {
          const fcose = (await import('cytoscape-fcose')).default as unknown;
          (cytoscape as unknown as { use: (ext: unknown) => void }).use(fcose);
          fcoseRegistered = true;
        } catch {
          if (layout === 'fcose') layout = 'cose';
        }
      }
      if (cy) {
        cy.destroy();
        cy = null;
      }
      const { nodes, edges } = filteredElements();
      const deg = degrees(edges);
      const maxDeg = Math.max(1, ...Object.values(deg));
      const elements = [
        ...nodes.map((node) => ({
          data: {
            id: node.id,
            label: node.label,
            kind: node.kind,
            workId: node.workId,
            doi: node.doi,
            year: node.year,
            venue: node.venue,
            deg: deg[node.id] ?? 0,
          },
        })),
        ...edges.map((edge) => ({
          data: {
            source: edge.source,
            target: edge.target,
            weight: edge.weight,
            resolution: edge.resolution,
          },
        })),
      ];
      const useLayout = layout === 'fcose' && !fcoseRegistered ? 'cose' : layout;
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
        layout: { name: useLayout, animate: false },
      });
      cy.on('tap', 'node', (event: { target: { data: (k: string) => unknown } }) => {
        const t = event.target;
        const workId = t.data('workId') as string | null;
        if (workId && onOpenWork) {
          onOpenWork(workId);
          return;
        }
        // External node (no local work): offer an import by DOI when possible (#8).
        const doi = t.data('doi') as string | null;
        if (!workId && doi && onImportExternal) onImportExternal(doi);
      });
      // Hover tooltips (#8): title / year / venue / DOI.
      cy.on('mouseover', 'node', (event: { target: { data: (k: string) => unknown; renderedPosition: () => { x: number; y: number } } }) => {
        const t = event.target;
        const parts = [
          `<strong>${escapeHtml(String(t.data('label') ?? ''))}</strong>`,
          [t.data('year'), t.data('venue')].filter(Boolean).map((v) => escapeHtml(String(v))).join(' · '),
          t.data('doi') ? `doi:${escapeHtml(String(t.data('doi')))}` : '',
        ].filter(Boolean);
        const pos = t.renderedPosition();
        tooltip = { show: true, x: pos.x, y: pos.y, html: parts.join('<br>') };
      });
      cy.on('mouseout', 'node', () => {
        tooltip = { ...tooltip, show: false };
      });
      cyError = false;
    } catch {
      // Cytoscape needs a canvas-capable DOM; fall back to the list renderer.
      cyError = true;
    }
  }

  function escapeHtml(s: string): string {
    return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c);
  }

  function relayout(): void {
    if (cy) cy.layout({ name: layout === 'fcose' && !fcoseRegistered ? 'cose' : layout, animate: false }).run();
  }

  // Re-render when the active graph, render mode, or client-side filters change.
  $: if (renderMode === 'graph' && hasGraph && cyContainer && (rNodes || hideSingletons || hideExternalLeaves)) void renderGraph();

  onDestroy(() => {
    if (cy) cy.destroy();
  });
</script>

<section>
  <div class="head">
    <h3>{graphType === 'topic' ? 'Topic graph' : 'Citation graph'} {label}</h3>
    <div class="controls">
      {#if loadTopic}
        <div class="seg" role="group" aria-label="Graph type">
          <button type="button" class:active={graphType === 'citation'}
            on:click={() => (graphType = 'citation')}
            title="Show citation links between papers">Citation</button>
          <button type="button" class:active={graphType === 'topic'}
            on:click={() => (graphType = 'topic')}
            title="Show embedding-similarity (topic) links between papers">Topic</button>
        </div>
      {/if}
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
          title="Show the graph edges as a plain list"
        >
          List
        </button>
      </div>
      {#if renderMode === 'graph'}
        <select bind:value={layout} on:change={relayout} disabled={disabled || busy}
          title="Graph layout algorithm">
          <option value="fcose">Force (fCoSE)</option>
          <option value="cose">Force (cose)</option>
          <option value="circle">Circle</option>
          <option value="grid">Grid</option>
          <option value="breadthfirst">Hierarchy</option>
        </select>
      {/if}
      {#if graphType === 'citation'}
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
      {/if}
      <label class="toggle" title="Hide nodes that have no edges">
        <input type="checkbox" bind:checked={hideSingletons}
          aria-label="Hide nodes with no edges" />
        Hide singletons
      </label>
      {#if graphType === 'citation' && nodeMode === 'include_external'}
        <label class="toggle" title="Hide external (outside-library) nodes and their edges">
          <input type="checkbox" bind:checked={hideExternalLeaves}
            aria-label="Hide external nodes" />
          Hide external
        </label>
      {/if}
      <button type="button" on:click={build} disabled={disabled || busy}
        title={graphType === 'topic' ? 'Build the topic graph for the chosen scope' : 'Build the citation graph for the chosen scope'}>Build graph</button>
    </div>
  </div>

  {#if hasGraph}
    {#if graphType === 'topic' && topicGraph}
      <p class="summary">
        {topicGraph.summary.node_count} nodes · {topicGraph.summary.edge_count} edges
        {#if topicGraph.summary.embedding_model} · {topicGraph.summary.embedding_model}{/if}
      </p>
      {#if !topicGraph.summary.used_embeddings}
        <p class="note">{topicGraph.summary.note ?? 'Topic graph is using a non-embedding fallback (no embeddings available).'}</p>
      {/if}
    {:else if graph}
      <p class="summary">
        {graph.summary.node_count ?? graph.nodes.length} nodes · {graph.summary.edge_count ??
          graph.edges.length} edges · {graph.summary.external_node_count ?? 0} external ·
        {graph.summary.unresolved_reference_count ?? 0} unresolved
      </p>
    {/if}

    {#if rNodes.length === 0 && rEdges.length === 0}
      <p class="empty">{graphType === 'topic' ? 'No similarity edges in this scope yet.' : 'No citation edges in this scope yet.'}</p>
    {:else if renderMode === 'graph'}
      <div class="cy-wrap">
        <div class="cy" bind:this={cyContainer}></div>
        {#if tooltip.show}
          <div class="cy-tooltip" style={`left:${tooltip.x + 12}px; top:${tooltip.y + 12}px`}>{@html tooltip.html}</div>
        {/if}
      </div>
      {#if cyError}
        <p class="empty">Interactive view unavailable here — switch to List.</p>
      {:else}
        <p class="hint">Node size ≈ degree · hover for details · click a local node to open it{onImportExternal ? ' (external nodes offer import)' : ''}.</p>
      {/if}
    {:else}
      <ul class="edges">
        {#each rEdges as edge (edge.source + '->' + edge.target)}
          <li>
            <span>{nodeLabel(edge.source)}</span>
            <span class="arrow">{graphType === 'topic' ? '↔' : '→'}</span>
            <span>{nodeLabel(edge.target)}</span>
            <small>{edge.resolution ?? `sim ${edge.weight.toFixed(2)}`}{edge.resolution && edge.weight > 1 ? ` ·×${edge.weight}` : ''}</small>
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

  .cy-wrap {
    position: relative;
  }

  .cy {
    background: #fbfcfd;
    border: 1px solid #d8dee6;
    border-radius: 6px;
    height: min(60vh, 34rem);
    width: 100%;
  }

  .cy-tooltip {
    background: #1f2a36;
    border-radius: 6px;
    color: #fff;
    font-size: 0.78rem;
    line-height: 1.35;
    max-width: 20rem;
    padding: 0.35rem 0.55rem;
    pointer-events: none;
    position: absolute;
    z-index: 5;
  }

  .note {
    background: #fef3c7;
    border-radius: 6px;
    color: #78350f;
    font-size: 0.82rem;
    margin: 0.2rem 0;
    padding: 0.35rem 0.55rem;
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
