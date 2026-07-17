<!-- CitationGraph — ECharts force/circular graph of citation or topic-similarity links, with a
     Graph/List render toggle, its own legend-chip filtering and ctrl-click neighborhood focus.
     Props: label, disabled, load (citation-graph fetcher), loadTopic (topic-graph fetcher,
     optional — enables the Citation/Topic toggle), onOpenWork, onImportExternal, visible.
     Events/callbacks: none exported — interactions are surfaced via onOpenWork/onImportExternal.
     Non-obvious lifecycle/state: node size/color/filter/layout changes are pure client-side
     ECharts option rebuilds (no refetch) driven by bumping `revision`; renderChart distinguishes
     a genuine data reload from a restyle-only repaint (same graph object) to decide between a
     full non-merge setOption (restarts the force sim) and a merge (preserves pan/zoom); a
     one-shot delayed `fitView` re-centers the force layout after it settles. -->
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
  import Modal from './Modal.svelte';
  import { pieSymbol } from '../lib/graphPie';

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
  let showCiting = true; // 2026-07-16: toggle citing papers (incoming citations) on/off
  let revision = 0;
  // 2026-07-16: surface build state (a large graph can take a while, and failures were silent).
  let buildState: 'idle' | 'building' | 'done' | 'failed' = 'idle';
  let buildError = '';

  async function build(): Promise<void> {
    busy = true;
    buildState = 'building';
    buildError = '';
    try {
      if (graphType === 'topic' && loadTopic) {
        topicGraph = await loadTopic();
        graph = null;
      } else {
        graph = await load(nodeMode, collapseVersions, colorBy);
        topicGraph = null;
      }
      // Fresh data means fresh groups — reset any legend-chip filtering + ctrl-click focus
      // (stale node ids from the previous dataset would otherwise hide everything).
      hiddenGroups = new Set();
      soloGroup = null;
      focusIds = null;
      focusLabel = '';
      revision += 1;
      scheduleForceFit();
      buildState = 'done';
    } catch (err) {
      buildState = 'failed';
      buildError = err instanceof Error ? err.message : String(err);
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
    citationCount: number | null;
    colorGroup: string | null;
    // ALL membership groups (shelf/rack/tag color-by): 2+ renders the node as a color wheel.
    colorGroups: string[] | null;
    warning: boolean;
  };

  // Topic-graph encodings (UX batch 4): its own selects — the citation metrics (pagerank etc.)
  // don't exist on similarity nodes, but citation count and year do.
  let topicSizeBy: 'degree' | 'citations' = 'degree';
  let topicColorBy: 'none' | 'year' | 'shelf' | 'rack' | 'tag' = 'none';
  type REdge = { source: string; target: string; weight: number; resolution?: string; relation?: string };

  $: rNodes = (() => {
    if (graphType === 'topic' && topicGraph) {
      return topicGraph.nodes.map<RNode>((n) => ({
        id: n.id, label: n.label, kind: 'local', workId: n.work_id, year: n.year,
        venue: n.venue ?? null, doi: n.doi ?? null, degree: 0, pagerank: 0, betweenness: 0,
        citationCount: n.citation_count ?? null,
        colorGroup:
          topicColorBy === 'year'
            ? String(n.year ?? 'unknown')
            : topicColorBy !== 'none'
              ? (n.memberships?.[topicColorBy]?.[0] ?? null)
              : null,
        colorGroups: topicColorBy !== 'none' && topicColorBy !== 'year'
          ? (n.memberships?.[topicColorBy] ?? null)
          : null,
        warning: false,
      }));
    }
    if (graph) {
      return graph.nodes.map<RNode>((n) => ({
        id: n.id, label: n.label, kind: n.type, workId: n.work_id, year: n.year,
        venue: n.venue ?? null, doi: n.doi, degree: n.degree ?? 0, pagerank: n.pagerank ?? 0,
        betweenness: n.betweenness ?? 0, citationCount: n.citation_count ?? null,
        colorGroup: n.color_group ?? null,
        colorGroups: n.color_groups ?? null,
        warning: n.warning ?? false,
      }));
    }
    return [] as RNode[];
  })();

  $: rEdges = (() => {
    if (graphType === 'topic' && topicGraph)
      return topicGraph.edges.map<REdge>((e) => ({ source: e.source, target: e.target, weight: e.weight }));
    if (graph)
      return graph.edges.map<REdge>((e) => ({ source: e.source, target: e.target, weight: e.weight, resolution: e.resolution, relation: e.relation }));
    return [] as REdge[];
  })();

  // Distinct color groups (citation graph only), sorted — years numerically (unknown last), the
  // rest alphabetically — so the legend chips and the palette progression read in order.
  $: colorGroups = (() => {
    const activeColorBy = graphType === 'topic' ? topicColorBy : colorBy;
    if (activeColorBy === 'none') return [] as string[];
    const seen: string[] = [];
    for (const n of rNodes) {
      for (const g of n.colorGroups ?? (n.colorGroup ? [n.colorGroup] : [])) {
        if (!seen.includes(g)) seen.push(g);
      }
    }
    if (activeColorBy === 'year') {
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
    // 2026-07-16: client-side hide of citing papers (external citers only — an in-library citer
    // stays as a normal local node) and their edges.
    if (!showCiting) for (const n of rNodes) if (n.id.startsWith('citing:')) hiddenIds.add(n.id);
    // Legend-chip filtering: groups the user toggled off (click) or excluded via solo (shift-click).
    if (hiddenGroups.size) {
      for (const n of rNodes) {
        const groups = n.colorGroups ?? (n.colorGroup ? [n.colorGroup] : []);
        if (groups.length && groups.every((g) => hiddenGroups.has(g))) hiddenIds.add(n.id);
      }
    }
    // Ctrl-click neighborhood focus (UX batch 3): only the focused node/category + direct
    // neighbors stay visible.
    if (focusIds) {
      for (const n of rNodes) if (!focusIds.has(n.id)) hiddenIds.add(n.id);
    }
    const visibleEdges = rEdges.filter(
      (e) =>
        !hiddenIds.has(e.source) &&
        !hiddenIds.has(e.target) &&
        (showCiting || e.relation !== 'citing'),
    );
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
          : (graphType === 'citation' && sizeBy === 'citations') ||
              (graphType === 'topic' && topicSizeBy === 'citations')
            ? (n.citationCount ?? 0)
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
      // 2026-07-16: an external node that carries a colour-group value (e.g. its year) conforms to
      // the chosen scheme like a local node — only its diamond shape marks it external. Externals
      // with no value for the active scheme fall back to the flat "external" colour.
      if (n.kind === 'external') {
        const idx = n.colorGroup ? colorGroups.indexOf(n.colorGroup) : -1;
        return idx >= 0 ? idx : colorGroups.length;
      }
      if (colorGroups.length === 0) return colorGroups.length + 1;
      return Math.max(0, colorGroups.indexOf(n.colorGroup ?? ''));
    };
    // Plotted-order indices per group, for the chips' hover highlight dispatch.
    groupDataIndices = new Map();
    nodes.forEach((n, i) => {
      for (const g of n.colorGroups ?? (n.colorGroup ? [n.colorGroup] : [])) {
        const arr = groupDataIndices.get(g) ?? [];
        arr.push(i);
        groupDataIndices.set(g, arr);
      }
    });
    return {
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: (params: { dataType?: string; data?: Record<string, unknown> }) => {
          if (params.dataType === 'edge') {
            const d = params.data as { source: string; target: string; weight: number; resolution?: string; relation?: string };
            const rel = d.relation === 'citing' ? 'cites' : graphType === 'topic' ? '↔' : '→';
            return `${nodeLabel(d.source)} ${rel} ${nodeLabel(d.target)}${
              d.resolution ? ` · ${d.resolution}` : ` · sim ${Number(d.weight).toFixed(2)}`
            }${d.relation === 'citing' ? ' · citing paper' : ''}`;
          }
          const d = params.data as { name?: string; meta?: RNode; sizeValue?: number };
          const m = d.meta;
          if (!m) return String(d.name ?? '');
          // Encoded channels (UX batch 3): spell out what size/color mean for THIS node.
          const sizeMetric =
            graphType === 'citation'
              ? sizeBy === 'citations'
                ? 'citation count'
                : sizeBy
              : topicSizeBy === 'citations'
                ? 'citation count'
                : 'degree (similarity links)';
          const sizeVal =
            d.sizeValue != null && Number.isFinite(d.sizeValue)
              ? Number(d.sizeValue) >= 1
                ? String(Math.round(Number(d.sizeValue)))
                : Number(d.sizeValue).toFixed(4)
              : '—';
          const activeColorBy = graphType === 'topic' ? topicColorBy : colorBy;
          const groupList = m.colorGroups?.length ? m.colorGroups.join(', ') : m.colorGroup;
          const colorDesc =
            groupList && activeColorBy !== 'none'
              ? `color = ${activeColorBy}: ${groupList}`
              : `color = ${m.kind === 'external' ? 'external (not in library)' : 'in library'}`;
          const bits = [
            `<strong>${m.label}</strong>`,
            [m.year, m.venue].filter(Boolean).join(' · '),
            m.doi ? `doi:${m.doi}` : '',
            m.kind === 'external' ? 'not in library' : '',
            `<span style="opacity:.75">size = ${sizeMetric}: ${sizeVal} · ${colorDesc}</span>`,
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
            // Multi-membership local nodes render as a color wheel (one segment per group);
            // externals keep their diamond, single-group nodes the plain (cheaper) circle.
            symbol:
              n.kind === 'external'
                ? 'diamond'
                : (n.colorGroups?.length ?? 0) > 1
                  ? pieSymbol(
                      (n.colorGroups as string[]).map(
                        (g) => groupColors[Math.max(0, colorGroups.indexOf(g))],
                      ),
                    )
                  : 'circle',
            symbolSize: max === min ? 22 : 12 + ((metric(n) - min) / (max - min)) * 30,
            itemStyle: n.warning
              ? { borderColor: viz.warningRing, borderWidth: 3 }
              : undefined,
            sizeValue: metric(n),
            meta: n,
          })),
          links: visibleEdges.map((e) => ({
            source: e.source,
            target: e.target,
            weight: e.weight,
            resolution: e.resolution,
            relation: e.relation,
            // 2026-07-16: colour citing edges (a paper → scope work) distinctly from references.
            lineStyle: {
              width: 1 + (e.weight / maxWeight) * 5,
              ...(e.relation === 'citing' ? { color: '#e07b39' } : {}),
            },
          })),
        },
      ],
    };
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let lastRenderedData: unknown = null;
  function renderChart(chart: any): void {
    // 2026-07-16 pan/zoom fix: restyle-only repaints (size/colour/filter/focus toggles bumped
    // `revision` WITHOUT new data) use a MERGE setOption, so the force simulation isn't restarted
    // and the user's current pan/zoom (roam transform) is preserved. Only a genuine data reload
    // (Build/Refresh swaps the graph object) does a full non-merge rebuild. The old code did a
    // non-merge rebuild on EVERY repaint, which restarted the sim and reset roam — the intermittent
    // "frozen pan/zoom" the user hit (a tab refocus briefly masked it).
    const dataRef = (graphType === 'topic' ? topicGraph : graph) as unknown;
    const isRestyle = dataRef !== null && dataRef === lastRenderedData;
    chart.setOption(buildOption(), !isRestyle);
    lastRenderedData = dataRef;
  }

  // --- Ctrl-click neighborhood focus (UX batch 3) ---------------------------------------------
  // Focus = the clicked node (or every node of a clicked category chip) plus its direct
  // neighbors; everything else is hidden. Ctrl-click the same target again, or Reset view,
  // to show everything.
  let focusIds: Set<string> | null = null;
  let focusLabel = '';

  function neighborhoodOf(seedIds: Set<string>): Set<string> {
    const keep = new Set(seedIds);
    for (const e of rEdges) {
      if (seedIds.has(e.source)) keep.add(e.target);
      if (seedIds.has(e.target)) keep.add(e.source);
    }
    return keep;
  }

  function focusOnNode(node: RNode): void {
    if (focusIds && focusLabel === node.label) {
      clearFocus();
      return;
    }
    focusIds = neighborhoodOf(new Set([node.id]));
    focusLabel = node.label;
    revision += 1;
  }

  function focusOnGroup(group: string): void {
    if (focusIds && focusLabel === group) {
      clearFocus();
      return;
    }
    const seeds = new Set(rNodes.filter((n) => n.colorGroup === group).map((n) => n.id));
    focusIds = neighborhoodOf(seeds);
    focusLabel = group;
    revision += 1;
  }

  function clearFocus(): void {
    focusIds = null;
    focusLabel = '';
    revision += 1;
  }

  // Standard graph buttons (UX batch 3). "Show all" resets only the roam view (zoom 1,
  // auto-center) — a full repaint would restart the force simulation and the nodes would spring
  // back out of view. "Reset view" clears every filter (chips, solo, ctrl-click focus) and
  // repaints; "Refresh" recomputes the data from the server, then resets. After any repaint of a
  // force layout, a one-shot fit re-centers the view once the springing has settled.
  function fitView(): void {
    // Merge-setOption of the official zoom/center view props — the layout keeps its positions.
    chartHost?.getChart()?.setOption({ series: [{ zoom: 1, center: null }] });
  }

  let fitTimer: ReturnType<typeof setTimeout> | null = null;
  function scheduleForceFit(): void {
    if (layout !== 'force') return;
    if (fitTimer) clearTimeout(fitTimer);
    fitTimer = setTimeout(() => {
      fitTimer = null;
      fitView();
    }, 1600);
  }

  function showAll(): void {
    fitView();
  }

  function resetView(): void {
    hiddenGroups = new Set();
    soloGroup = null;
    focusIds = null;
    focusLabel = '';
    revision += 1;
    scheduleForceFit();
  }

  async function refresh(): Promise<void> {
    resetView();
    await build();
    scheduleForceFit();
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function wireEvents(chart: any): void {
    chart.on(
      'click',
      (params: {
        dataType?: string;
        data?: { meta?: RNode };
        event?: { event?: MouseEvent };
      }) => {
        if (params.dataType !== 'node') return;
        const node = params.data?.meta;
        if (!node) return;
        const raw = params.event?.event;
        if (raw && (raw.ctrlKey || raw.metaKey)) {
          focusOnNode(node);
          return;
        }
        if (node.workId && onOpenWork) {
          onOpenWork(node.workId);
          return;
        }
        if (!node.workId && node.doi && onImportExternal) onImportExternal(node.doi);
      },
    );
  }

  // --- Legend chips (our own legend; see the buildOption comment on why not ECharts') ---

  let chartHost: ChartHost | null = null;

  // Click: toggle one group. Shift-click: show only that group; shift-click it again to show all.
  // Ctrl-click: focus the group + its direct neighbors (UX batch 3).
  function onChipClick(group: string, shiftKey: boolean, ctrlKey = false): void {
    if (ctrlKey) {
      focusOnGroup(group);
      return;
    }
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
    topicSizeBy;
    topicColorBy;
    hideSingletons;
    hideExternalLeaves;
    showCiting;
    layout;
    revision += 1;
  }

  // Help popup (UX batch 4): what the metrics mean + how the edges are computed, per graph type.
  let showHelp = false;
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
          <option value="citations">Size: citation count</option>
        </select>
        <select bind:value={colorBy} on:change={() => { if (graph) void build(); }}
          disabled={disabled || busy} data-testid="graph-color-by"
          title="Group node colors by an attribute (rebuilds to fetch groups)">
          <option value="none">Color: none</option>
          <option value="status">Color: reading status</option>
          <option value="shelf">Color: shelf</option>
          <option value="rack">Color: rack</option>
          <option value="tag">Color: tag</option>
          <option value="topic">Color: topic</option>
          <option value="year">Color: year</option>
        </select>
      {/if}
      {#if graphType === 'topic' && renderMode === 'graph'}
        <select bind:value={topicSizeBy} disabled={disabled || busy} data-testid="topic-size-by"
          title="What node size represents">
          <option value="degree">Size: similarity links</option>
          <option value="citations">Size: citation count</option>
        </select>
        <select bind:value={topicColorBy} disabled={disabled || busy} data-testid="topic-color-by"
          title="Group node colors by an attribute">
          <option value="none">Color: none</option>
          <option value="year">Color: year</option>
          <option value="shelf">Color: shelf</option>
          <option value="rack">Color: rack</option>
          <option value="tag">Color: tag</option>
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
        <label class="toggle" title="Show papers that CITE the scope (incoming citations, orange edges) — from data you've fetched">
          <input type="checkbox" bind:checked={showCiting} aria-label="Show citing papers" />
          Citing papers
        </label>
      {/if}
      <button type="button" on:click={build} disabled={disabled || busy}
        title={graphType === 'topic' ? 'Build the topic graph for the chosen scope' : 'Build the citation graph for the chosen scope'}
        >{buildState === 'building' ? 'Building…' : 'Build graph'}</button>
      {#if buildState === 'building'}
        <small class="build-state muted" role="status">Building the graph…</small>
      {:else if buildState === 'failed'}
        <small class="build-state failed" role="alert">Build failed: {buildError || 'see the console / Jobs tab'}</small>
      {:else if buildState === 'done' && !hasGraph}
        <small class="build-state muted" role="status">Built — no nodes for this scope.</small>
      {/if}
      {#if graphType === 'citation' && nodeMode === 'include_external' && hasGraph && showCiting && graph?.summary && !(graph.summary as Record<string, unknown>).citing_available}
        <small class="build-state muted" role="status" title="Fetch citing papers from a paper's Citing-papers panel or the library batch action">No citing papers fetched for this scope yet.</small>
      {/if}
      {#if hasGraph}
        <button type="button" class="secondary" on:click={showAll} disabled={busy}
          title="Fit the view so the whole graph is visible again (keeps filters)">Show all</button>
        <button type="button" class="secondary" on:click={resetView} disabled={busy}
          title="Fit the view AND clear every filter (chips, solo, ctrl-click focus)">Reset view</button>
        <button type="button" class="secondary" on:click={refresh} disabled={disabled || busy}
          title="Recompute the graph from the server, then reset the view and filters">Refresh</button>
      {/if}
      <button type="button" class="secondary" on:click={() => (showHelp = true)}
        data-testid="graph-help" title="What the metrics mean and how the edges are computed">ⓘ Help</button>
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
      {#if colorGroups.length}
        <div class="chips" role="group" aria-label="Color groups">
          {#each colorGroups as group, i (group)}
            <button
              type="button"
              class="chip"
              class:off={hiddenGroups.has(group)}
              on:click={(e) => onChipClick(group, e.shiftKey, e.ctrlKey || e.metaKey)}
              on:mouseenter={() => onChipHover(group, true)}
              on:mouseleave={() => onChipHover(group, false)}
              title="Hover: highlight this group · Click: show/hide it · Shift-click: show only this group (shift-click again to show all) · Ctrl-click: show this group + its direct neighbors"
            >
              <span class="dot" style={`background:${groupColors[i]}`}></span>{group}
            </button>
          {/each}
        </div>
      {/if}
      {#if focusIds}
        <p class="note" data-testid="graph-focus-note">
          Focused on “{focusLabel}” + direct neighbors — ctrl-click it again or use Reset view to
          show everything.
        </p>
      {/if}
      <ChartHost bind:this={chartHost} render={renderChart} onReady={wireEvents} {revision} {visible}
        ariaLabel={graphType === 'topic' ? 'Topic graph' : 'Citation graph'}>
        <svelte:fragment slot="fallback">Interactive view unavailable here — switch to List.</svelte:fragment>
      </ChartHost>
      <p class="hint">Node size ≈ {graphType === 'citation' ? (sizeBy === 'citations' ? 'citation count' : sizeBy) : topicSizeBy === 'citations' ? 'citation count' : 'similarity links'} · red ring = review warning · hover for details · click an in-library node to open it{onImportExternal ? ' (external nodes offer import)' : ''} · ctrl-click a node to show only it + neighbors.{#if colorGroups.length}
          Color chips: hover highlights, click hides/shows, shift-click solos, ctrl-click focuses a group + neighbors.{/if}</p>
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

{#if showHelp}
  <Modal title={graphType === 'topic' ? 'About the topic graph' : 'About the citation graph'}
    onClose={() => (showHelp = false)}>
    {#if graphType === 'topic'}
      <dl class="graph-help">
        <dt>What it shows</dt>
        <dd>Each node is a paper in the scope; an edge means the two papers are semantically
          similar. There are no citation relations here — it maps what the papers are
          <em>about</em>.</dd>
        <dt>How edges are computed</dt>
        <dd>Every paper's title + abstract is embedded with the active embedding model; edges are
          the cosine similarity between those vectors. Each paper keeps only its top-6 most
          similar neighbours, and pairs below 0.30 similarity are dropped — so the graph stays
          sparse and readable. Edge width ∝ similarity. Without a real embedding model there are
          no edges (the note above the graph says so).</dd>
        <dt>Node size</dt>
        <dd><strong>Similarity links</strong>: how many similarity edges the paper has (a hub of
          its semantic neighbourhood). <strong>Citation count</strong>: the paper's global
          citation count from stored metadata.</dd>
        <dt>Color</dt>
        <dd>Uniform by default; <em>Color: year</em> groups papers by publication year (chips
          above the graph filter them).</dd>
      </dl>
    {:else}
      <dl class="graph-help">
        <dt>What it shows</dt>
        <dd>Each node is a paper; a directed edge A → B means A cites B (resolved from extracted
          references). External (not-in-library) cited papers appear as diamonds when included.</dd>
        <dt>Node size metrics</dt>
        <dd><strong>Degree</strong>: how many citation edges touch the paper — raw connectedness.
          <strong>PageRank</strong>: influence — a paper cited by other influential papers scores
          higher than one cited the same number of times by isolated papers.
          <strong>Betweenness</strong>: bridge-ness — how often the paper sits on the shortest
          path between two other papers; high values are papers connecting otherwise separate
          areas. <strong>Citation count</strong>: the paper's global (external) citation count from
          stored metadata — its real-world impact, independent of this scope's edges.</dd>
        <dt>Color</dt>
        <dd><em>Color: status/shelf/tag/topic/year</em> groups papers by that attribute (computed
          server-side; the chips above the graph filter the groups).</dd>
      </dl>
    {/if}
    <dl class="graph-help">
      <dt>Interactions</dt>
      <dd>Click an in-library node to open it{onImportExternal ? '; click an external node to import it' : ''}.
        Ctrl-click a node or a color chip → show only it + direct neighbours (ctrl-click again or
        Reset view to clear). Shift-click a chip solos its group. Show all fits the view; Refresh
        recomputes from the server.</dd>
    </dl>
  </Modal>
{/if}

<style>
  .graph-help dt {
    font-weight: 600;
    margin-top: 0.6rem;
  }

  .graph-help dd {
    margin: 0.15rem 0 0;
  }

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

  .build-state {
    align-self: center;
    font-size: 0.82rem;
  }
  .build-state.failed {
    color: var(--status-error, #c0392b);
    font-weight: 600;
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
