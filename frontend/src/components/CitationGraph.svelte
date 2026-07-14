<script lang="ts">
  // Citation / topic graph on ECharts (owner decision 2026-07-13: one charting stack — the
  // previous Cytoscape renderer was the only non-ECharts surface). Force-directed `graph` series;
  // node size, colors, filters and theme switches are pure client-side option rebuilds (no
  // refetch); only color_by needs the server (it computes the groups).
  import type {
    CitationGraphResponse,
    GraphColorBy,
    GraphNodeMode,
    GraphSizeBy,
    TopicGraphResponse,
  } from '../api/client';
  import { activeVizTheme } from '../lib/theme/store';
  import ChartHost from './ChartHost.svelte';

  export let label = '';
  export let disabled = false;
  export let load: (
    nodeMode: GraphNodeMode,
    collapseVersions: boolean,
    colorBy: GraphColorBy,
  ) => Promise<CitationGraphResponse> = async () => ({ nodes: [], edges: [], summary: {} });
  // Topic (embedding-similarity) graph loader (#6): adds the Citation/Topic mode toggle.
  export let loadTopic: (() => Promise<TopicGraphResponse>) | null = null;
  export let onOpenWork: ((workId: string) => void) | null = null;
  export let onImportExternal: ((doi: string) => void) | null = null;
  export let visible = true;

  let graphType: 'citation' | 'topic' = 'citation';
  let nodeMode: GraphNodeMode = 'local_only';
  let collapseVersions = false;
  let sizeBy: GraphSizeBy = 'degree';
  let colorBy: GraphColorBy = 'none';
  let renderMode: 'graph' | 'list' = 'graph';
  let layout: 'force' | 'circular' = 'force';
  let graph: CitationGraphResponse | null = null;
  let topicGraph: TopicGraphResponse | null = null;
  let busy = false;
  let hideSingletons = true;
  let hideExternalLeaves = false;
  let revision = 0;

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
      revision += 1;
    } finally {
      busy = false;
    }
  }

  // Unified node/edge shape for rendering, derived from whichever graph is active.
  type RNode = {
    id: string;
    label: string;
    kind: 'local' | 'external';
    workId: string | null;
    year: number | null;
    venue: string | null;
    doi: string | null;
    degree: number;
    pagerank: number;
    betweenness: number;
    colorGroup: string | null;
    warning: boolean;
  };
  type REdge = { source: string; target: string; weight: number; resolution?: string };

  $: rNodes = (() => {
    if (graphType === 'topic' && topicGraph) {
      return topicGraph.nodes.map<RNode>((n) => ({
        id: n.id, label: n.label, kind: 'local', workId: n.work_id, year: n.year,
        venue: n.venue ?? null, doi: n.doi ?? null, degree: 0, pagerank: 0, betweenness: 0,
        colorGroup: null, warning: false,
      }));
    }
    if (graph) {
      return graph.nodes.map<RNode>((n) => ({
        id: n.id, label: n.label, kind: n.type, workId: n.work_id, year: n.year,
        venue: n.venue ?? null, doi: n.doi, degree: n.degree ?? 0, pagerank: n.pagerank ?? 0,
        betweenness: n.betweenness ?? 0, colorGroup: n.color_group ?? null,
        warning: n.warning ?? false,
      }));
    }
    return [] as RNode[];
  })();

  $: rEdges = (() => {
    if (graphType === 'topic' && topicGraph)
      return topicGraph.edges.map<REdge>((e) => ({ source: e.source, target: e.target, weight: e.weight }));
    if (graph)
      return graph.edges.map<REdge>((e) => ({ source: e.source, target: e.target, weight: e.weight, resolution: e.resolution }));
    return [] as REdge[];
  })();

  // Distinct color groups (citation graph only), in first-seen order — ECharts categories/legend.
  $: colorGroups = (() => {
    if (graphType !== 'citation' || colorBy === 'none') return [] as string[];
    const seen: string[] = [];
    for (const n of rNodes) if (n.colorGroup && !seen.includes(n.colorGroup)) seen.push(n.colorGroup);
    return seen;
  })();

  $: hasGraph = graphType === 'topic' ? topicGraph != null : graph != null;
  $: activeSummary =
    graphType === 'topic'
      ? (topicGraph?.summary as Record<string, number> | null) ?? null
      : (graph?.summary as Record<string, number> | null) ?? null;

  function nodeLabel(id: string): string {
    return rNodes.find((node) => node.id === id)?.label ?? id;
  }

  function clientDegrees(edges: REdge[]): Record<string, number> {
    const deg: Record<string, number> = {};
    for (const edge of edges) {
      deg[edge.source] = (deg[edge.source] ?? 0) + edge.weight;
      deg[edge.target] = (deg[edge.target] ?? 0) + edge.weight;
    }
    return deg;
  }

  // Build the whole ECharts option from the current data + toggles. Filters (hide singletons /
  // externals), sizing and colors are all client-side rebuilds — no refetch.
  function buildOption(): Record<string, unknown> {
    const viz = $activeVizTheme;
    const hiddenIds = new Set<string>();
    if (hideExternalLeaves) for (const n of rNodes) if (n.kind === 'external') hiddenIds.add(n.id);
    const visibleEdges = rEdges.filter((e) => !hiddenIds.has(e.source) && !hiddenIds.has(e.target));
    if (hideSingletons) {
      const touched = new Set<string>();
      for (const e of visibleEdges) {
        touched.add(e.source);
        touched.add(e.target);
      }
      for (const n of rNodes) if (!hiddenIds.has(n.id) && !touched.has(n.id)) hiddenIds.add(n.id);
    }
    const nodes = rNodes.filter((n) => !hiddenIds.has(n.id));
    const deg = clientDegrees(visibleEdges);
    const metric = (n: RNode): number =>
      graphType === 'citation' && sizeBy === 'pagerank'
        ? n.pagerank
        : graphType === 'citation' && sizeBy === 'betweenness'
          ? n.betweenness
          : n.degree || deg[n.id] || 0;
    const values = nodes.map(metric);
    const max = Math.max(0, ...values);
    const min = Math.min(0, ...values);
    const maxWeight = Math.max(1, ...visibleEdges.map((e) => e.weight));
    // Explicit per-category colors (not the option-level palette): with palette-assigned colors,
    // ECharts' legend-hover highlight repaints nodes in the emphasis default instead of their own
    // group color. An explicit itemStyle survives the emphasis state, so hover highlights correctly.
    const categories = [
      ...colorGroups.map((g, i) => ({
        name: g,
        itemStyle: { color: viz.categorical[i % viz.categorical.length] },
      })),
      { name: 'external', itemStyle: { color: viz.nodeDefault } },
      ...(colorGroups.length === 0
        ? [{ name: 'in library', itemStyle: { color: viz.categorical[0] } }]
        : []),
    ];
    const categoryIndex = (n: RNode): number => {
      if (n.kind === 'external') return colorGroups.length;
      if (colorGroups.length === 0) return colorGroups.length + 1;
      return Math.max(0, colorGroups.indexOf(n.colorGroup ?? ''));
    };
    return {
      legend: colorGroups.length
        ? [{ data: colorGroups, textStyle: { color: viz.text }, top: 0 }]
        : undefined,
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: (params: { dataType?: string; data?: Record<string, unknown> }) => {
          if (params.dataType === 'edge') {
            const d = params.data as { source: string; target: string; weight: number; resolution?: string };
            return `${nodeLabel(d.source)} ${graphType === 'topic' ? '↔' : '→'} ${nodeLabel(d.target)}${
              d.resolution ? ` · ${d.resolution}` : ` · sim ${Number(d.weight).toFixed(2)}`
            }`;
          }
          const d = params.data as { name?: string; meta?: RNode };
          const m = d.meta;
          if (!m) return String(d.name ?? '');
          const bits = [
            `<strong>${m.label}</strong>`,
            [m.year, m.venue].filter(Boolean).join(' · '),
            m.doi ? `doi:${m.doi}` : '',
            m.kind === 'external' ? 'not in library' : '',
          ].filter(Boolean);
          return bits.join('<br>');
        },
      },
      series: [
        {
          type: 'graph',
          layout,
          circular: { rotateLabel: true },
          force: { repulsion: 120, edgeLength: [40, 120], gravity: 0.08 },
          roam: true,
          draggable: true,
          label: {
            show: true,
            position: 'right',
            color: viz.text,
            fontSize: 9,
            overflow: 'truncate',
            width: 120,
            formatter: (p: { data?: { meta?: RNode } }) => p.data?.meta?.label ?? '',
          },
          emphasis: { focus: 'adjacency' },
          categories,
          edgeSymbol: graphType === 'citation' ? ['none', 'arrow'] : ['none', 'none'],
          edgeSymbolSize: 6,
          lineStyle: { color: viz.edge, opacity: 0.7, curveness: 0.15 },
          data: nodes.map((n) => ({
            id: n.id,
            name: n.label,
            category: categoryIndex(n),
            symbol: n.kind === 'external' ? 'diamond' : 'circle',
            symbolSize: max === min ? 22 : 12 + ((metric(n) - min) / (max - min)) * 30,
            itemStyle: n.warning
              ? { borderColor: viz.warningRing, borderWidth: 3 }
              : undefined,
            meta: n,
          })),
          links: visibleEdges.map((e) => ({
            source: e.source,
            target: e.target,
            weight: e.weight,
            resolution: e.resolution,
            lineStyle: { width: 1 + (e.weight / maxWeight) * 5 },
          })),
        },
      ],
    };
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function renderChart(chart: any): void {
    // A full option rebuild resets the legend selection, so any solo state is gone too.
    soloGroup = null;
    chart.setOption(buildOption(), true);
  }

  // Shift-click legend solo: show only the clicked color group; shift-clicking the soloed group
  // again re-selects all. ECharts legend events carry no modifier keys, so the shift state is
  // captured from the DOM click (capture phase runs before ECharts' own handler).
  let soloGroup: string | null = null;
  let lastClickShift = false;
  let applyingLegend = false;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function wireEvents(chart: any): void {
    chart.on('click', (params: { dataType?: string; data?: { meta?: RNode } }) => {
      if (params.dataType !== 'node') return;
      const node = params.data?.meta;
      if (!node) return;
      if (node.workId && onOpenWork) {
        onOpenWork(node.workId);
        return;
      }
      if (!node.workId && node.doi && onImportExternal) onImportExternal(node.doi);
    });
    chart
      .getDom()
      ?.addEventListener('click', (e: MouseEvent) => (lastClickShift = e.shiftKey), true);
    chart.on(
      'legendselectchanged',
      (params: { name: string; selected: Record<string, boolean> }) => {
        if (applyingLegend) return; // our own dispatched actions re-fire this event
        if (!lastClickShift) {
          soloGroup = null; // a plain toggle breaks any solo state
          return;
        }
        applyingLegend = true;
        try {
          const groups = Object.keys(params.selected);
          const leavingSolo = soloGroup === params.name;
          for (const group of groups) {
            chart.dispatchAction({
              type: leavingSolo || group === params.name ? 'legendSelect' : 'legendUnSelect',
              name: group,
            });
          }
          soloGroup = leavingSolo ? null : params.name;
        } finally {
          applyingLegend = false;
        }
      },
    );
  }

  // Client-side option rebuilds: size/filter/layout toggles bump the revision (ChartHost repaints).
  $: {
    sizeBy;
    hideSingletons;
    hideExternalLeaves;
    layout;
    revision += 1;
  }
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
        <button type="button" class:active={renderMode === 'graph'}
          on:click={() => (renderMode = 'graph')}
          title="Show an interactive node-link graph">Graph</button>
        <button type="button" class:active={renderMode === 'list'}
          on:click={() => (renderMode = 'list')}
          title="Show the graph edges as a plain list">List</button>
      </div>
      {#if renderMode === 'graph'}
        <select bind:value={layout} disabled={disabled || busy} title="Graph layout algorithm">
          <option value="force">Force</option>
          <option value="circular">Circle</option>
        </select>
      {/if}
      {#if graphType === 'citation'}
        <select bind:value={nodeMode} disabled={disabled || busy}
          title="Whether to include not-in-library papers as external nodes">
          <option value="local_only">In library only</option>
          <option value="include_external">Include external</option>
        </select>
        <label class="toggle" title="Merge papers linked as versions of one another into one node">
          <input type="checkbox" bind:checked={collapseVersions} disabled={disabled || busy}
            aria-label="Collapse works linked as versions into one node" />
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
          <option value="year">Color: year</option>
        </select>
      {/if}
      <label class="toggle" title="Hide nodes that have no edges">
        <input type="checkbox" bind:checked={hideSingletons} aria-label="Hide nodes with no edges" />
        Hide singletons
      </label>
      {#if graphType === 'citation' && nodeMode === 'include_external'}
        <label class="toggle" title="Hide external (not-in-library) nodes and their edges">
          <input type="checkbox" bind:checked={hideExternalLeaves} aria-label="Hide external nodes" />
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
        {#if activeSummary?.nodes_hidden}&nbsp;· {activeSummary.nodes_hidden} hidden by the node cap{/if}
      </p>
      {#if !topicGraph.summary.used_embeddings}
        <p class="note">{topicGraph.summary.note ?? 'Topic graph is using a non-embedding fallback (no embeddings available).'}</p>
      {/if}
    {:else if graph}
      <p class="summary">
        {graph.summary.node_count ?? graph.nodes.length} nodes · {graph.summary.edge_count ??
          graph.edges.length} edges · {graph.summary.external_node_count ?? 0} external ·
        {graph.summary.unresolved_reference_count ?? 0} unresolved
        {#if activeSummary?.nodes_hidden}&nbsp;· {activeSummary.nodes_hidden} hidden by the node cap{/if}
        {#if activeSummary?.external_hidden}&nbsp;· {activeSummary.external_hidden} external hidden by the external cap{/if}
      </p>
    {/if}

    {#if rNodes.length === 0 && rEdges.length === 0}
      <p class="empty">{graphType === 'topic' ? 'No similarity edges in this scope yet.' : 'No citation edges in this scope yet.'}</p>
    {:else if renderMode === 'graph'}
      <ChartHost render={renderChart} onReady={wireEvents} {revision} {visible}
        ariaLabel={graphType === 'topic' ? 'Topic graph' : 'Citation graph'}>
        <svelte:fragment slot="fallback">Interactive view unavailable here — switch to List.</svelte:fragment>
      </ChartHost>
      <p class="hint">Node size ≈ {graphType === 'citation' ? sizeBy : 'degree'} · red ring = review warning · hover for details · click an in-library node to open it{onImportExternal ? ' (external nodes offer import)' : ''}.{#if colorGroups.length}
          Legend: click toggles a color group; shift-click shows only that group (shift-click it again to show all).{/if}</p>
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
