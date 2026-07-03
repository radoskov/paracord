<script lang="ts">
  import { onDestroy } from 'svelte';

  import type {
    CitationGraphResponse,
    GraphColorBy,
    GraphNodeMode,
    GraphSizeBy,
    TopicGraphResponse,
  } from '../api/client';
  import { resolveThemeById, type VizTheme } from '../lib/viz/theme';
  import { activeThemeId } from '../lib/theme/store';

  export let label = '';
  export let disabled = false;
  export let load: (
    nodeMode: GraphNodeMode,
    collapseVersions: boolean,
    colorBy: GraphColorBy,
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

  // Resize (and re-lay-out) the graph whenever its container's box changes — initial flex/tab
  // layout, window resize, tab show/hide. Cytoscape lays nodes out within the container size at
  // layout time, so a graph built while the container was tiny/hidden clusters on the left; a bare
  // resize() only grows the canvas, so we also relayout (debounced) to spread nodes to the new
  // width. Disconnected on destroy.
  let resizeObserver: ResizeObserver | null = null;
  let resizeTimer: ReturnType<typeof setTimeout> | null = null;
  $: if (cyContainer && typeof ResizeObserver !== 'undefined') observeContainer(cyContainer);
  function observeContainer(el: HTMLElement): void {
    if (resizeObserver) resizeObserver.disconnect();
    resizeObserver = new ResizeObserver(() => {
      if (!cy) return;
      cy.resize();
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (cy) relayout();
      }, 150);
    });
    resizeObserver.observe(el);
  }

  // Graph type: citation (default) or topic (embedding similarity, #6).
  let graphType: 'citation' | 'topic' = 'citation';
  let nodeMode: GraphNodeMode = 'local_only';
  let collapseVersions = false;
  // §8.9 depth (Track C P5b). size_by re-styles the live graph client-side (all three centrality
  // metrics ship on every node); color_by needs server-computed groups, so changing it refetches.
  let sizeBy: GraphSizeBy = 'degree';
  let colorBy: GraphColorBy = 'none';
  // Network colours come from the ACTIVE theme's validated `graph` block (categorical palette,
  // node/edge/label/grid/warning-ring) so the network is legible + on-theme under every theme.
  // Groups map to the categorical palette by first-seen order; the legend shows it. P3 wires live
  // re-styling: `restyle()` re-reads the palette and re-applies the stylesheet on the live instance
  // (no rebuild, no relayout) when the theme switches.
  function activeViz(): VizTheme {
    return resolveThemeById(
      typeof document !== 'undefined' ? document.documentElement.getAttribute('data-theme') : null,
    );
  }
  let viz = activeViz();
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
  // Topology signature of the currently-rendered graph — a matching refetch re-styles in place.
  let lastTopoSig = '';
  // Hover tooltip (#8).
  let tooltip = { show: false, x: 0, y: 0, html: '' };

  async function build(): Promise<void> {
    busy = true;
    try {
      if (graphType === 'topic' && loadTopic) {
        topicGraph = await loadTopic();
        graph = null;
      } else {
        graph = await load(nodeMode, collapseVersions, colorBy);
        topicGraph = null;
      }
    } finally {
      busy = false;
    }
  }

  // Unified node/edge shape for rendering, derived from whichever graph is active.
  type RNode = { id: string; label: string; kind: 'local' | 'external'; workId: string | null; year: number | null; venue: string | null; doi: string | null; degree: number; pagerank: number; betweenness: number; colorGroup: string | null; warning: boolean };
  type REdge = { source: string; target: string; weight: number; resolution?: string };

  $: rNodes = (() => {
    if (graphType === 'topic' && topicGraph) {
      return topicGraph.nodes.map<RNode>((n) => ({ id: n.id, label: n.label, kind: 'local', workId: n.work_id, year: n.year, venue: n.venue ?? null, doi: n.doi ?? null, degree: 0, pagerank: 0, betweenness: 0, colorGroup: null, warning: false }));
    }
    if (graph) {
      return graph.nodes.map<RNode>((n) => ({ id: n.id, label: n.label, kind: n.type, workId: n.work_id, year: n.year, venue: n.venue ?? null, doi: n.doi, degree: n.degree ?? 0, pagerank: n.pagerank ?? 0, betweenness: n.betweenness ?? 0, colorGroup: n.color_group ?? null, warning: n.warning ?? false }));
    }
    return [] as RNode[];
  })();

  // Distinct color groups (citation graph only), in first-seen order, for the palette + legend.
  $: colorGroups = (() => {
    if (graphType !== 'citation' || colorBy === 'none') return [] as string[];
    const seen: string[] = [];
    for (const n of rNodes) if (n.colorGroup && !seen.includes(n.colorGroup)) seen.push(n.colorGroup);
    return seen;
  })();

  function colorFor(group: string | null): string {
    if (!group) return viz.categorical[0];
    const i = colorGroups.indexOf(group);
    return viz.categorical[i % viz.categorical.length];
  }

  function metricOf(n: RNode): number {
    return sizeBy === 'pagerank' ? n.pagerank : sizeBy === 'betweenness' ? n.betweenness : n.degree;
  }
  $: rEdges = (() => {
    if (graphType === 'topic' && topicGraph) return topicGraph.edges.map<REdge>((e) => ({ source: e.source, target: e.target, weight: e.weight }));
    if (graph) return graph.edges.map<REdge>((e) => ({ source: e.source, target: e.target, weight: e.weight, resolution: e.resolution }));
    return [] as REdge[];
  })();

  // Apply the client-side filters (#7 hide singletons, #8 collapse external leaves) to the live
  // Cytoscape instance by showing/hiding elements — no rebuild, no re-layout (D17). The parameters
  // let the reactive caller below re-run this whenever a toggle flips. Mirrors the old filter logic:
  // hide external nodes + their edges, then hide any node left with no visible edge.
  function applyFilters(
    hideExt: boolean = hideExternalLeaves,
    hideSing: boolean = hideSingletons,
  ): void {
    if (!cy) return;
    const hiddenNodes = new Set<string>();
    if (hideExt) {
      for (const n of rNodes) if (n.kind === 'external') hiddenNodes.add(n.id);
    }
    const hiddenEdges = new Set<number>();
    rEdges.forEach((e, index) => {
      if (hiddenNodes.has(e.source) || hiddenNodes.has(e.target)) hiddenEdges.add(index);
    });
    if (hideSing) {
      const touched = new Set<string>();
      rEdges.forEach((e, index) => {
        if (hiddenEdges.has(index)) return;
        touched.add(e.source);
        touched.add(e.target);
      });
      for (const n of rNodes) if (!hiddenNodes.has(n.id) && !touched.has(n.id)) hiddenNodes.add(n.id);
    }
    cy.batch(() => {
      cy.nodes().forEach((node: { id: () => string; style: (k: string, v: string) => void }) =>
        node.style('display', hiddenNodes.has(node.id()) ? 'none' : 'element'),
      );
      cy.edges().forEach((edge: { id: () => string; style: (k: string, v: string) => void }) =>
        edge.style('display', hiddenEdges.has(Number(edge.id().slice(1))) ? 'none' : 'element'),
      );
    });
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

  // The Cytoscape stylesheet, derived from the active theme's `graph` block. Shared by the initial
  // build and the live `restyle()` so a theme switch repaints the running graph without a rebuild.
  function buildStyle(v: VizTheme, maxWeight: number): unknown[] {
    return [
      {
        selector: 'node',
        style: {
          label: 'data(label)',
          'font-size': 9,
          'text-wrap': 'ellipsis',
          'text-max-width': '120px',
          color: v.text,
          'background-color': 'data(color)',
          width: 28,
          height: 28,
        },
      },
      {
        selector: 'node[kind = "external"]',
        style: { 'background-color': v.nodeDefault, shape: 'diamond' },
      },
      {
        // Warning badge (§8.9): a ring (theme danger colour) around nodes with a review warning
        // (multiwork / duplicate / unresolved) so problem papers stand out at a glance.
        selector: 'node[warn = 1]',
        style: { 'border-width': 3, 'border-color': v.warningRing, 'border-opacity': 0.95 },
      },
      {
        selector: 'edge',
        style: {
          width: `mapData(weight, 1, ${maxWeight}, 1, 8)`,
          'line-color': v.edge,
          'target-arrow-color': v.edge,
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
        },
      },
    ];
  }

  // Live restyle on theme change (P3): re-read the theme, re-derive each node's categorical colour,
  // and swap the stylesheet on the live instance. No rebuild, no relayout — positions are kept.
  function restyle(): void {
    if (!cy) return;
    viz = activeViz();
    const maxWeight = Math.max(1, ...rEdges.map((e) => e.weight));
    cy.batch(() => {
      for (const node of rNodes) {
        const el = cy.getElementById(node.id);
        if (el.length) el.data('color', colorFor(node.colorGroup));
      }
    });
    cy.style(buildStyle(viz, maxWeight)).update();
  }

  // Reading $activeThemeId makes this reactive; applyTheme has already updated data-theme by the
  // time the store publishes, so `activeViz()` inside restyle() resolves the new palette.
  $: if ($activeThemeId && cy) restyle();

  async function renderGraph(): Promise<void> {
    if (!hasGraph || !cyContainer) return;
    viz = activeViz();
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
      // Topology signature: node id set + edge count. A color_by refetch keeps the same topology
      // (only node data changes), so we re-style the live instance in place — no rebuild, no
      // relayout (D17). Only a real topology change (node_mode / collapse / scope) rebuilds.
      const sig = `${rNodes.map((n) => n.id).sort().join(',')}|${rEdges.length}`;
      if (cy && sig === lastTopoSig) {
        cy.batch(() => {
          for (const node of rNodes) {
            const el = cy.getElementById(node.id);
            if (el.length) {
              el.data('color', colorFor(node.colorGroup));
              el.data('warn', node.warning ? 1 : 0);
            }
          }
        });
        applySizing();
        applyFilters();
        return;
      }
      if (cy) {
        cy.destroy();
        cy = null;
      }
      lastTopoSig = sig;
      // Build the FULL element set once (edges carry a stable `e{index}` id). The client-side
      // filters (#7/#8) are applied by showing/hiding elements on the live instance (D17), so a
      // filter toggle no longer rebuilds the graph or re-runs the layout. Node size (applySizing) and
      // color/warning are per-node data so a size_by / color_by change re-styles without a relayout.
      const maxWeight = Math.max(1, ...rEdges.map((e) => e.weight));
      const elements = [
        ...rNodes.map((node) => ({
          data: {
            id: node.id,
            label: node.label,
            kind: node.kind,
            workId: node.workId,
            doi: node.doi,
            year: node.year,
            venue: node.venue,
            color: colorFor(node.colorGroup),
            warn: node.warning ? 1 : 0,
          },
        })),
        ...rEdges.map((edge, index) => ({
          data: {
            id: `e${index}`,
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
        style: buildStyle(viz, maxWeight),
        // Positions come from the explicit relayout() below, which lays out only the visible
        // subset so filtered-out nodes don't leave gaps.
        layout: { name: 'preset' },
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
      applySizing();
      applyFilters();
      relayout();
    } catch {
      // Cytoscape needs a canvas-capable DOM; fall back to the list renderer.
      cyError = true;
    }
  }

  // Re-size nodes on the live instance from the chosen size_by metric — no rebuild, no relayout
  // (D17). Degree falls back to the client-side incident-weight sum (covers the topic graph, whose
  // nodes carry no server centrality). Betweenness/pagerank only exist on the citation graph.
  function applySizing(): void {
    if (!cy) return;
    const clientDeg = degrees(rEdges);
    const valueOf = (n: RNode): number => {
      if (graphType === 'citation' && sizeBy === 'pagerank') return n.pagerank;
      if (graphType === 'citation' && sizeBy === 'betweenness') return n.betweenness;
      return n.degree || clientDeg[n.id] || 0;
    };
    const byId = new Map(rNodes.map((n) => [n.id, valueOf(n)]));
    const vals = [...byId.values()];
    const max = Math.max(0, ...vals);
    const min = Math.min(0, ...vals);
    cy.batch(() => {
      cy.nodes().forEach((node: { id: () => string; style: (k: string, v: number) => void }) => {
        const v = byId.get(node.id()) ?? 0;
        const size = max === min ? 28 : 16 + ((v - min) / (max - min)) * 40;
        node.style('width', size);
        node.style('height', size);
      });
    });
  }

  function escapeHtml(s: string): string {
    return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c);
  }

  function relayout(): void {
    if (!cy) return;
    const name = layout === 'fcose' && !fcoseRegistered ? 'cose' : layout;
    // Lay out only the currently-visible subset so filtered-out nodes don't leave gaps.
    cy.elements(':visible').layout({ name, animate: false }).run();
  }

  // Rebuild + lay out only when the underlying data or the render surface changes (D17) — NOT on a
  // filter toggle. rNodes/rEdges are fresh arrays whenever a new graph is loaded/built.
  $: if (renderMode === 'graph' && hasGraph && cyContainer && rNodes && rEdges) void renderGraph();

  // A filter toggle just shows/hides elements on the live instance — no rebuild, no re-layout (D17).
  $: if (cy && renderMode === 'graph') applyFilters(hideExternalLeaves, hideSingletons);

  // A size_by change just re-sizes the live nodes — no rebuild, no relayout (D17).
  $: {
    sizeBy;
    if (cy && renderMode === 'graph') applySizing();
  }

  onDestroy(() => {
    if (resizeObserver) resizeObserver.disconnect();
    if (resizeTimer) clearTimeout(resizeTimer);
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
      {#if graphType === 'citation' && renderMode === 'graph'}
        <select bind:value={sizeBy} disabled={disabled || busy} data-testid="graph-size-by"
          title="What node size represents">
          <option value="degree">Size: degree</option>
          <option value="pagerank">Size: PageRank</option>
          <option value="betweenness">Size: betweenness</option>
        </select>
        <select bind:value={colorBy} on:change={() => { if (graph) void build(); }}
          disabled={disabled || busy} data-testid="graph-color-by"
          title="Group node colors by an attribute (rebuilds to fetch groups)">
          <option value="none">Color: none</option>
          <option value="status">Color: reading status</option>
          <option value="shelf">Color: shelf</option>
          <option value="tag">Color: tag</option>
          <option value="topic">Color: topic</option>
        </select>
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
      {#if graphType === 'citation' && colorBy !== 'none' && colorGroups.length > 0}
        <ul class="legend" data-testid="graph-legend" aria-label="Color legend">
          {#each colorGroups as group (group)}
            <li><span class="swatch" style={`background:${colorFor(group)}`}></span>{group}</li>
          {/each}
        </ul>
      {/if}
      {#if cyError}
        <p class="empty">Interactive view unavailable here — switch to List.</p>
      {:else}
        <p class="hint">Node size ≈ {graphType === 'citation' ? sizeBy : 'degree'} · red ring = review warning · hover for details · click a local node to open it{onImportExternal ? ' (external nodes offer import)' : ''}.</p>
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
    color: var(--ink-strong);
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
    background: var(--surface-overlay);
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    color: var(--ink-strong);
    cursor: pointer;
    font: inherit;
    font-weight: 700;
    min-height: 2rem;
    padding: 0.3rem 0.6rem;
  }

  button.active {
    background: var(--accent-primary);
    color: var(--ink-inverse);
  }

  select {
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    font: inherit;
    padding: 0.3rem 0.5rem;
  }

  .cy-wrap {
    position: relative;
  }

  .cy {
    background: var(--surface-raised);
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    height: min(60vh, 34rem);
    width: 100%;
  }

  .cy-tooltip {
    background: var(--ink-strong);
    border-radius: 6px;
    color: var(--surface-base);
    font-size: 0.78rem;
    line-height: 1.35;
    max-width: 20rem;
    padding: 0.35rem 0.55rem;
    pointer-events: none;
    position: absolute;
    z-index: 5;
  }

  .note {
    background: var(--status-warning-bg);
    border-radius: 6px;
    color: var(--status-warning);
    font-size: 0.82rem;
    margin: 0.2rem 0;
    padding: 0.35rem 0.55rem;
  }

  .summary,
  .hint {
    color: var(--ink-muted);
    font-size: 0.85rem;
  }

  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1rem;
    list-style: none;
    margin: 0.4rem 0 0;
    padding: 0;
  }

  .legend li {
    align-items: center;
    color: var(--ink-strong);
    display: flex;
    font-size: 0.8rem;
    gap: 0.35rem;
  }

  .swatch {
    border-radius: 3px;
    display: inline-block;
    height: 0.8rem;
    width: 0.8rem;
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
    color: var(--ink-muted);
  }
</style>
