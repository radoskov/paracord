<script lang="ts">
  import { onMount } from 'svelte';

  import type { ApiClient, ReferenceGraph, ReferenceGraphNode } from '../api/client';
  import ChartHost from './ChartHost.svelte';
  import Modal from './Modal.svelte';
  import { activeVizTheme } from '../lib/theme/store';
  import { enableLegendSolo } from '../lib/viz/legendSolo';
  import { pendingImportText, pendingLibraryOpen } from '../lib/selection';
  import {
    DEFAULT_SECTION_WEIGHTS,
    REFERENCE_GRAPH_HELP,
    REFERENCE_Y_AXES,
    buildReferenceGraphOption,
    yValueFor,
  } from '../lib/viz/referenceGraph';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;
  export let workId: string;
  export let onClose: () => void;

  let graph: ReferenceGraph | null = null;
  let weights: Record<string, number> = { ...DEFAULT_SECTION_WEIGHTS };
  let includeRefEdges = false;
  let includeCiting = true;
  // Base limit 500 (UX batch 3); the user's chosen value persists in their preferences blob.
  let maxExternal = 500;
  let yAxis = 'weighted';
  let colorBy = 'kind';
  let showHelp = false;

  // How many reference nodes have no value for the current Y axis (→ the dashed "n/a" lane).
  $: naCount = graph
    ? graph.nodes.filter((n) => n.kind !== 'base' && yValueFor(n, yAxis, weights) == null).length
    : 0;
  let loading = true;
  let message = '';
  let revision = 0;

  // Build a free-text citation line for the batch-import box from an external reference (5g):
  // "Title (year)" — the user refines it and picks from the import search results.
  function citationLine(node: ReferenceGraphNode): string {
    const title = node.label?.trim() || 'Untitled reference';
    return node.year != null ? `${title} (${node.year})` : title;
  }

  // A single node click: open the paper it is/likely-is in the Library, else prefill Import (5g).
  function openOrImportNode(node: ReferenceGraphNode): void {
    if (node.kind === 'base') return;
    // A likely_local reference isn't resolved but has a candidate work to review.
    const openId =
      node.resolved_work_id ??
      (node.kind === 'likely_local' ? (node.suggested_work_id ?? null) : null);
    if (openId) {
      pendingLibraryOpen.set(openId);
      if (typeof window !== 'undefined') window.location.hash = '#library';
      onClose();
    } else {
      pendingImportText.set(citationLine(node));
      if (typeof window !== 'undefined') window.location.hash = '#import';
      onClose();
    }
  }

  // Delegate clicks on the enterable tooltip's links (an overlap-cluster lists its members): open a
  // single member, or prefill the Import box with every not-in-library member of the cluster.
  function onContainerClick(ev: MouseEvent): void {
    const target = ev.target as HTMLElement | null;
    const openEl = target?.closest('[data-viz-open]');
    if (openEl) {
      const node = graph?.nodes.find((n) => n.id === openEl.getAttribute('data-viz-open'));
      if (node) openOrImportNode(node);
      return;
    }
    const importEl = target?.closest('[data-viz-import-all]');
    if (importEl) {
      const ids = (importEl.getAttribute('data-viz-import-all') ?? '').split(',').filter(Boolean);
      const lines = ids
        .map((id) => graph?.nodes.find((n) => n.id === id))
        .filter((n): n is ReferenceGraphNode => !!n)
        .map(citationLine);
      if (lines.length) {
        pendingImportText.set(lines.join('\n'));
        if (typeof window !== 'undefined') window.location.hash = '#import';
        onClose();
      }
    }
  }

  async function loadWeights(): Promise<void> {
    try {
      const prefs = await client.getPreferences();
      if (prefs.citation_section_weights) {
        weights = { ...DEFAULT_SECTION_WEIGHTS, ...prefs.citation_section_weights };
      }
      if (typeof prefs.reference_graph_max_external === 'number') {
        maxExternal = Math.min(500, Math.max(0, prefs.reference_graph_max_external));
      }
    } catch {
      // keep defaults if preferences can't be read
    }
  }

  // Persist the chosen max-external limit (merge-put so other preference keys survive).
  async function persistMaxExternal(): Promise<void> {
    try {
      const prefs = await client.getPreferences();
      await client.putPreferences({ ...prefs, reference_graph_max_external: maxExternal });
    } catch {
      // best-effort — the value still applies for this session
    }
  }

  function onMaxExternalChange(): void {
    void loadGraph();
    void persistMaxExternal();
  }

  async function loadGraph(): Promise<void> {
    loading = true;
    message = '';
    try {
      graph = await client.referenceGraph(workId, { includeRefEdges, includeCiting, maxExternal });
      // Fresh data → stale focus ids would hide everything.
      focusIds = null;
      focusLabel = '';
      revision += 1;
    } catch (error) {
      message = errorMessage(error);
    } finally {
      loading = false;
    }
  }

  // --- Ctrl-click neighborhood focus (UX batch 3, parity with the Insights graph) -------------
  let focusIds: Set<string> | null = null;
  let focusLabel = '';

  function neighborhoodOf(seed: Set<string>): Set<string> {
    const keep = new Set(seed);
    for (const e of graph?.edges ?? []) {
      if (seed.has(e.source)) keep.add(e.target);
      if (seed.has(e.target)) keep.add(e.source);
    }
    return keep;
  }

  function clearFocus(): void {
    focusIds = null;
    focusLabel = '';
    revision += 1;
  }

  function focusOnNode(node: ReferenceGraphNode): void {
    if (focusIds && focusLabel === node.id) {
      clearFocus();
      return;
    }
    focusIds = neighborhoodOf(new Set([node.id]));
    focusLabel = node.id;
    revision += 1;
  }

  // Ctrl-click a legend entry: focus that whole category + the direct neighbors of its nodes
  // (e.g. "external" shows every external reference plus this paper). Node ids are read from the
  // clicked series' plotted data, so it works for kind AND venue coloring alike.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function focusOnLegendSeries(chart: any, name: string): void {
    if (focusIds && focusLabel === name) {
      clearFocus();
      return;
    }
    const series = ((chart.getOption()?.series ?? []) as {
      name?: string;
      type?: string;
      data?: { node?: ReferenceGraphNode; members?: ReferenceGraphNode[] }[];
    }[]).find((s) => s.name === name && s.type === 'scatter');
    const seeds = new Set<string>();
    for (const d of series?.data ?? []) {
      if (d?.members) for (const m of d.members) seeds.add(m.id);
      else if (d?.node) seeds.add(d.node.id);
    }
    if (!seeds.size) return;
    focusIds = neighborhoodOf(seeds);
    focusLabel = name;
    revision += 1;
  }

  $: focusLabelText =
    focusIds && graph
      ? (graph.nodes.find((n) => n.id === focusLabel)?.label ?? focusLabel)
      : '';

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function renderChart(chart: any): void {
    if (!graph) return;
    const g = focusIds
      ? {
          ...graph,
          nodes: graph.nodes.filter((n) => focusIds?.has(n.id)),
          edges: graph.edges.filter((e) => focusIds?.has(e.source) && focusIds?.has(e.target)),
        }
      : graph;
    chart.setOption(buildReferenceGraphOption(g, weights, $activeVizTheme, { yAxis, colorBy }), true);
  }

  // Edge-snapped cursor zoom (UX batch 3): when a wheel-zoom happens with the cursor in the
  // outer ~15% of the chart, pin the zoom window to that data edge. Fixes the zoom-drag-zoom
  // dance toward the bottom lanes (citing papers / "no year" items): put the cursor near the
  // bottom and the vertical window stays glued to the lowest nodes while the horizontal zoom
  // remains cursor-anchored. One corrective dispatch per wheel tick — no rendering overhead.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function wireEdgeSnapZoom(chart: any): void {
    const dom = chart.getDom?.();
    if (!dom) return;
    const EDGE = 0.15;
    dom.addEventListener(
      'wheel',
      (ev: WheelEvent) => {
        requestAnimationFrame(() => {
          const rect = dom.getBoundingClientRect();
          if (!rect.width || !rect.height) return;
          const xFrac = (ev.clientX - rect.left) / rect.width;
          const yFrac = (ev.clientY - rect.top) / rect.height;
          const dzs = (chart.getOption()?.dataZoom ?? []) as { start?: number; end?: number }[];
          const snap = (idx: number, toStart: boolean): void => {
            const z = dzs[idx];
            if (!z) return;
            const span = (z.end ?? 100) - (z.start ?? 0);
            if (span >= 99.5) return; // fully zoomed out — nothing to pin
            chart.dispatchAction(
              toStart
                ? { type: 'dataZoom', dataZoomIndex: idx, start: 0, end: span }
                : { type: 'dataZoom', dataZoomIndex: idx, start: 100 - span, end: 100 },
            );
          };
          if (xFrac < EDGE) snap(0, true);
          else if (xFrac > 1 - EDGE) snap(0, false);
          if (yFrac > 1 - EDGE) snap(1, true); // screen bottom = low axis values
          else if (yFrac < EDGE) snap(1, false);
        });
      },
      { passive: true },
    );
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function wireEvents(chart: any): void {
    chart.on(
      'click',
      (params: { data?: { node?: ReferenceGraphNode }; event?: { event?: MouseEvent } }) => {
        const node = params.data?.node;
        if (!node) return;
        const raw = params.event?.event;
        if (raw && (raw.ctrlKey || raw.metaKey)) {
          focusOnNode(node); // ctrl-click = neighborhood focus, NOT open/import (UX batch 3)
          return;
        }
        openOrImportNode(node);
      },
    );
    // Track modifier state at the DOM level (legendselectchanged carries no modifiers), so a
    // ctrl-click on a legend entry becomes a neighborhood focus instead of a hide/show toggle.
    let legendCtrl = false;
    chart.getDom()?.addEventListener(
      'click',
      (ev: MouseEvent) => {
        legendCtrl = ev.ctrlKey || ev.metaKey;
      },
      true,
    );
    chart.on('legendselectchanged', (params: { name?: string }) => {
      if (!legendCtrl || !params.name) return;
      legendCtrl = false;
      chart.dispatchAction({ type: 'legendAllSelect' }); // undo the toggle the click caused
      focusOnLegendSeries(chart, params.name);
    });
    // Delegate clicks on the enterable-tooltip links (overlap clusters) at the container level.
    chart.getDom()?.addEventListener('click', onContainerClick);
    // Shift-click a legend entry to show only that kind/venue; shift-click again to show all.
    enableLegendSolo(chart);
    wireEdgeSnapZoom(chart);
  }

  // Y-axis / colour changes are pure client-side restyles — bump the revision, no refetch.
  $: {
    yAxis;
    colorBy;
    revision += 1;
  }

  // Standard graph buttons (UX batch 3). Scatter charts zoom via inside dataZoom, so "Show all"
  // resets both axes to their full extent; "Reset view" also restores every legend kind
  // (deselected via click / shift-click solo); "Refresh" refetches and recomputes.
  let chartHost: ChartHost | null = null;
  function rgShowAll(): void {
    const chart = chartHost?.getChart();
    chart?.dispatchAction({ type: 'dataZoom', start: 0, end: 100, dataZoomIndex: 0 });
    chart?.dispatchAction({ type: 'dataZoom', start: 0, end: 100, dataZoomIndex: 1 });
  }
  function rgResetView(): void {
    rgShowAll();
    chartHost?.getChart()?.dispatchAction({ type: 'legendAllSelect' });
    if (focusIds) clearFocus();
  }

  function toggleRefEdges(): void {
    includeRefEdges = !includeRefEdges;
    void loadGraph();
  }

  function toggleCiting(): void {
    includeCiting = !includeCiting;
    void loadGraph();
  }

  onMount(async () => {
    await loadWeights();
    await loadGraph();
  });
</script>

<Modal title="Reference graph" wide {onClose}>
  <div class="rg-controls">
    <label title="What the vertical axis plots for each reference">
      Y axis
      <select bind:value={yAxis} data-testid="rg-y-axis">
        {#each REFERENCE_Y_AXES as opt (opt.key)}<option value={opt.key}>{opt.label}</option>{/each}
      </select>
    </label>
    <label title="How node colour is grouped">
      Colour
      <select bind:value={colorBy} data-testid="rg-color-by">
        <option value="kind">Kind (this paper / in library / external)</option>
        <option value="venue">Venue</option>
      </select>
    </label>
    <label title="Also draw citation links between the in-library references that cite each other">
      <input type="checkbox" checked={includeRefEdges} on:change={toggleRefEdges} data-testid="rg-ref-edges" />
      Local reference-to-reference edges
    </label>
    <label title="Also show the external papers that cite this one (fetched in the paper's Citing-papers panel), as incoming nodes">
      <input type="checkbox" checked={includeCiting} on:change={toggleCiting} data-testid="rg-citing" />
      Show citing papers
    </label>
    <label title="Keep only this many external references / citing papers (the most-cited and newest); in-library nodes are never hidden">
      Max external
      <input type="number" min="0" max="500" bind:value={maxExternal} on:change={onMaxExternalChange}
        class="max-external" data-testid="rg-max-external" />
    </label>
    <button type="button" class="secondary" on:click={rgShowAll}
      title="Zoom out so every node is visible again">Show all</button>
    <button type="button" class="secondary" on:click={rgResetView}
      title="Show all + restore every legend kind (clears click/shift-click filtering)">Reset view</button>
    <button type="button" class="secondary" on:click={() => loadGraph()} disabled={loading}
      title="Refetch and recompute the graph">Refresh</button>
    {#if focusIds}
      <span class="muted" data-testid="rg-focus-note"
        >Focused: {focusLabelText} + neighbors — ctrl-click again or Reset view</span>
    {/if}
    <span class="muted"
      >X = year · node size = section-weighted citations (weights in Profile) · the highlighted node
      is this paper.{#if naCount > 0}
        {naCount} reference{naCount === 1 ? '' : 's'} have no value for this axis (dashed, on the
        “n/a” lane).{/if}</span
    >
    <button
      type="button"
      class="help"
      data-testid="rg-help"
      on:click={() => (showHelp = true)}
      title="What each axis and setting means, and how to read the graph"
    >
      ⓘ Help
    </button>
  </div>
  {#if message}<p class="msg" role="status">{message}</p>{/if}
  {#if loading && !graph}<p class="muted">Loading…</p>{/if}
  <div class="rg-chart" data-testid="rg-chart">
    <ChartHost bind:this={chartHost} render={renderChart} onReady={wireEvents} {revision} height="100%"
      ariaLabel="Reference graph">
      <svelte:fragment slot="fallback">Interactive chart unavailable in this environment.</svelte:fragment>
    </ChartHost>
  </div>
  {#if graph && graph.nodes.length <= 1}
    <p class="muted">This paper has no extracted references yet — run extraction to populate them.</p>
  {/if}
</Modal>

{#if showHelp}
  <Modal title="About the reference graph" onClose={() => (showHelp = false)}>
    <p>{REFERENCE_GRAPH_HELP.overview}</p>
    <h4>Layout &amp; encodings</h4>
    <dl class="rg-help">
      <dt>X axis</dt>
      <dd>{REFERENCE_GRAPH_HELP.xAxis}</dd>
      <dt>Node size</dt>
      <dd>{REFERENCE_GRAPH_HELP.size}</dd>
      <dt>Colour</dt>
      <dd>{REFERENCE_GRAPH_HELP.color}</dd>
      <dt>“n/a” lane</dt>
      <dd>{REFERENCE_GRAPH_HELP.naLane}</dd>
    </dl>
    <h4>Y axis options</h4>
    <dl class="rg-help">
      {#each REFERENCE_Y_AXES as opt (opt.key)}
        <dt>{opt.label}</dt>
        <dd>{opt.help}</dd>
      {/each}
    </dl>
    <h4>Other settings</h4>
    <dl class="rg-help">
      <dt>Local reference-to-reference edges</dt>
      <dd>{REFERENCE_GRAPH_HELP.refEdges}</dd>
    </dl>
  </Modal>
{/if}

<style>
  .rg-controls {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.8rem;
    margin-bottom: 0.5rem;
  }
  .rg-chart {
    height: 60vh;
    min-height: 360px;
    width: 100%;
  }
  .muted {
    color: var(--ink-muted);
    font-size: 0.85rem;
  }
  .help {
    background: none;
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    color: var(--ink-strong);
    cursor: pointer;
    font: inherit;
    font-size: 0.8rem;
    margin-left: auto;
    padding: 0.25rem 0.6rem;
  }
  .rg-help {
    margin: 0.3rem 0 0.6rem;
  }
  .rg-help dt {
    color: var(--ink-strong);
    font-weight: 600;
  }
  .rg-help dd {
    color: var(--ink-muted);
    font-size: 0.85rem;
    margin: 0 0 0.5rem;
  }
</style>
