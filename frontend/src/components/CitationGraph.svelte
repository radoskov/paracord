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
  import { categoricalPalette } from '../lib/viz/theme';
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
      // Fresh data means fresh groups — reset any legend-chip filtering.
      hiddenGroups = new Set();
      soloGroup = null;
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

  // Distinct color groups (citation graph only), sorted — years numerically (unknown last), the
  // rest alphabetically — so the legend chips and the palette progression read in order.
  $: colorGroups = (() => {
    if (graphType !== 'citation' || colorBy === 'none') return [] as string[];
    const seen: string[] = [];
    for (const n of rNodes) if (n.colorGroup && !seen.includes(n.colorGroup)) seen.push(n.colorGroup);
    if (colorBy === 'year') {
      return seen.sort(
        (a, b) =>
          (a === 'unknown' ? 1 : 0) - (b === 'unknown' ? 1 : 0) || Number(a) - Number(b),
      );
    }
    return seen.sort((a, b) => a.localeCompare(b));
  })();

  // Colors for the groups; grows beyond the fixed theme palette (evenly spaced hues) so ~18 years
  // don't cycle the same 6 colors three times.
  $: groupColors = categoricalPalette(Math.max(1, colorGroups.length), $activeVizTheme);

  // Legend-chip state: groups toggled off (client-side node filter, like the other toggles).
  let hiddenGroups = new Set<string>();
  let soloGroup: string | null = null;
  // The plotted node order of the last built option, per group — for hover highlight dispatch.
  let groupDataIndices = new Map<string, number[]>();

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
    // Legend-chip filtering: groups the user toggled off (click) or excluded via solo (shift-click).
    if (hiddenGroups.size) {
      for (const n of rNodes) if (n.colorGroup && hiddenGroups.has(n.colorGroup)) hiddenIds.add(n.id);
    }
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
    // Explicit per-category colors (never the option-level palette). The legend is our own chip
    // row above the chart — the native graph-series legend resolves its hover against the NODE
    // list, so it highlighted whatever node sat at the legend item's index.
    const categories = [
      ...colorGroups.map((g, i) => ({
        name: g,
        itemStyle: { color: groupColors[i] },
      })),
      { name: 'external', itemStyle: { color: viz.nodeDefault } },
      ...(colorGroups.length === 0
        ? [{ name: 'in library', itemStyle: { color: groupColors[0] } }]
        : []),
    ];
    const categoryIndex = (n: RNode): number => {
      if (n.kind === 'external') return colorGroups.length;
      if (colorGroups.length === 0) return colorGroups.length + 1;
      return Math.max(0, colorGroups.indexOf(n.colorGroup ?? ''));
    };
    // Plotted-order indices per group, for the chips' hover highlight dispatch.
    groupDataIndices = new Map();
    nodes.forEach((n, i) => {
      if (!n.colorGroup) return;
      const arr = groupDataIndices.get(n.colorGroup) ?? [];
      arr.push(i);
      groupDataIndices.set(n.colorGroup, arr);
    });
    return {
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
    chart.setOption(buildOption(), true);
  }

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
  }

  // --- Legend chips (our own legend; see the buildOption comment on why not ECharts') ---

  let chartHost: ChartHost | null = null;

  // Click: toggle one group. Shift-click: show only that group; shift-click it again to show all.
  function onChipClick(group: string, shiftKey: boolean): void {
    if (shiftKey) {
      if (soloGroup === group) {
        hiddenGroups = new Set();
        soloGroup = null;
      } else {
        hiddenGroups = new Set(colorGroups.filter((g) => g !== group));
        soloGroup = group;
      }
    } else {
      const next = new Set(hiddenGroups);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      hiddenGroups = next;
      soloGroup = null;
    }
    revision += 1;
  }

  // Hover: emphasize the group's nodes (adjacency focus blurs the rest, so the color pops).
  function onChipHover(group: string, entering: boolean): void {
    const chart = chartHost?.getChart();
    const dataIndex = groupDataIndices.get(group);
    if (!chart || !dataIndex?.length) return;
    chart.dispatchAction({ type: entering ? 'highlight' : 'downplay', seriesIndex: 0, dataIndex });
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
      {#if graphType === 'citation' && colorGroups.length}
        <div class="chips" role="group" aria-label="Color groups">
          {#each colorGroups as group, i (group)}
            <button
              type="button"
              class="chip"
              class:off={hiddenGroups.has(group)}
              on:click={(e) => onChipClick(group, e.shiftKey)}
              on:mouseenter={() => onChipHover(group, true)}
              on:mouseleave={() => onChipHover(group, false)}
              title="Hover: highlight this group · Click: show/hide it · Shift-click: show only this group (shift-click again to show all)"
            >
              <span class="dot" style={`background:${groupColors[i]}`}></span>{group}
            </button>
          {/each}
        </div>
      {/if}
      <ChartHost bind:this={chartHost} render={renderChart} onReady={wireEvents} {revision} {visible}
        ariaLabel={graphType === 'topic' ? 'Topic graph' : 'Citation graph'}>
        <svelte:fragment slot="fallback">Interactive view unavailable here — switch to List.</svelte:fragment>
      </ChartHost>
      <p class="hint">Node size ≈ {graphType === 'citation' ? sizeBy : 'degree'} · red ring = review warning · hover for details · click an in-library node to open it{onImportExternal ? ' (external nodes offer import)' : ''}.{#if colorGroups.length}
          Color chips: hover highlights, click hides/shows, shift-click solos a group.{/if}</p>
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

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    margin: 0.35rem 0;
  }

  .chip {
    align-items: center;
    background: var(--surface-overlay);
    border: 1px solid var(--border-normal);
    border-radius: 999px;
    color: var(--ink-strong);
    cursor: pointer;
    display: inline-flex;
    font-size: 0.78rem;
    font-weight: 600;
    gap: 0.3rem;
    min-height: 0;
    padding: 0.1rem 0.55rem;
  }

  .chip.off {
    opacity: 0.45;
  }

  .chip.off .dot {
    background: var(--border-normal) !important;
  }

  .chip .dot {
    border-radius: 50%;
    display: inline-block;
    height: 0.65rem;
    width: 0.65rem;
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
