<!-- ReferenceGraphModal — modal wrapping the interactive reference/citation scatter graph for one
     paper (ECharts via ChartHost). Props: client (ApiClient), workId, onClose.
     Events/callbacks: onClose only.
     Non-obvious lifecycle/state: loads section weights + the graph on mount; Y-axis/colour/ref-edge
     toggles are pure client-side restyles driven by bumping `revision` (no refetch), while
     max-external and "show citing papers" changes refetch via loadGraph(). Ctrl-click on a node or
     legend series sets `focusIds` to restrict the rendered graph to a neighborhood (see
     neighborhoodOf/renderChart) without touching the fetched `graph` data itself. -->
<script lang="ts">
  import { onMount } from 'svelte';

  import type { ApiClient, ReferenceGraph, ReferenceGraphNode } from '../api/client';
  import ChartHost from './ChartHost.svelte';
  import Modal from './Modal.svelte';
  import { activeVizTheme } from '../lib/theme/store';
  import ColorGroupChips from '../lib/viz/ColorGroupChips.svelte';
  import { nextChipState, orHiddenIds } from '../lib/viz/colorGroups';
  import { pendingImportText, pendingLibraryOpen } from '../lib/selection';
  import {
    DEFAULT_SECTION_WEIGHTS,
    REFERENCE_GRAPH_HELP,
    REFERENCE_Y_AXES,
    buildReferenceGraphOption,
    referenceColorGroups,
    referenceNodeGroups,
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

  // 2026-07-16: append an external reference to the Batch-import box WITHOUT leaving the graph, so
  // several can be queued by ctrl/middle-clicking. The Import tab prefills from this store later.
  let appendedCount = 0;
  // The base paper's title for the modal header (from the graph payload, once loaded).
  $: baseTitle = graph?.nodes.find((n) => n.kind === 'base')?.label ?? '';
  function isExternalNode(node: ReferenceGraphNode): boolean {
    const openId =
      node.resolved_work_id ??
      (node.kind === 'likely_local' ? (node.suggested_work_id ?? null) : null);
    return node.kind !== 'base' && !openId;
  }
  function appendImport(node: ReferenceGraphNode): void {
    const line = citationLine(node);
    pendingImportText.update((cur) => (cur ? `${cur}\n${line}` : line));
    appendedCount += 1;
    message = `Added ${appendedCount} paper${appendedCount === 1 ? '' : 's'} to the import box — open the Import tab when ready.`;
  }

  // Live case-insensitive filter for the grouped-node tooltip's paper list (2026-07-16): hide the
  // member rows whose title/year don't match, in place (the enterable tooltip stays put while the
  // mouse is over it, so typing is stable). Purely DOM — no re-render, no framework state.
  function onTooltipSearch(ev: Event): void {
    const input = (ev.target as HTMLElement | null)?.closest(
      '[data-viz-search]',
    ) as HTMLInputElement | null;
    if (!input) return;
    // Stop the event reaching ECharts, which otherwise re-ran the tooltip formatter on the typing
    // and blew away the filtered list (the "only one paper shows" symptom) — 2026-07-16.
    ev.stopPropagation();
    const box = input.parentElement?.querySelector('[data-viz-members]');
    if (!box) return;
    const q = input.value.trim().toLowerCase();
    box.querySelectorAll('a').forEach((a) => {
      const hit = !q || (a.textContent ?? '').toLowerCase().includes(q);
      (a as HTMLElement).style.display = hit ? 'block' : 'none';
    });
  }

  // Keep keystrokes inside the tooltip's search box from reaching ECharts / the graph shortcuts
  // (which would move the view or re-render the tooltip out from under the filter).
  function onTooltipKey(ev: KeyboardEvent): void {
    if ((ev.target as HTMLElement | null)?.closest?.('[data-viz-search]')) ev.stopPropagation();
  }

  // Delegate clicks on the enterable tooltip's links (an overlap-cluster lists its members): open a
  // single member, or prefill the Import box with every not-in-library member of the cluster.
  // ctrl / middle-click on an external member appends it to the import box without switching tabs.
  function onContainerClick(ev: MouseEvent): void {
    const target = ev.target as HTMLElement | null;
    const openEl = target?.closest('[data-viz-open]');
    if (openEl) {
      const node = graph?.nodes.find((n) => n.id === openEl.getAttribute('data-viz-open'));
      if (!node) return;
      if ((ev.ctrlKey || ev.metaKey || ev.button === 1) && isExternalNode(node)) {
        ev.preventDefault();
        appendImport(node);
      } else {
        openOrImportNode(node);
      }
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
      // Always fetch the local ref→ref edges so the checkbox can toggle them CLIENT-SIDE (no
      // refetch / view reset); the toggle only controls their visibility (2026-07-16).
      graph = await client.referenceGraph(workId, {
        includeRefEdges: true,
        includeCiting,
        maxExternal,
      });
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

  // --- Color-group legend chips (OR filter + hover highlight over multi-membership nodes) --------
  // The node colors are driven by these chips (not the native ECharts legend, which can only toggle
  // a whole series and so can't express "keep the node while ANY of its colors is shown").
  let hiddenGroups = new Set<string>();
  let soloGroup: string | null = null;
  let highlightGroup: string | null = null;
  $: refLegend = graph
    ? referenceColorGroups(graph, colorBy, $activeVizTheme)
    : { groups: [] as string[], colors: [] as string[] };
  // A colour scheme switch invalidates the old group names → clear any chip filter/highlight.
  let prevColorBy = colorBy;
  $: if (colorBy !== prevColorBy) {
    prevColorBy = colorBy;
    hiddenGroups = new Set();
    soloGroup = null;
    highlightGroup = null;
  }

  function onChipToggle(e: CustomEvent<{ group: string; shiftKey: boolean; ctrlKey: boolean }>): void {
    const { group, shiftKey, ctrlKey } = e.detail;
    if (ctrlKey) {
      focusOnGroup(group);
      return;
    }
    const next = nextChipState(group, shiftKey, refLegend.groups, hiddenGroups, soloGroup);
    hiddenGroups = next.hidden;
    soloGroup = next.solo;
    revision += 1;
  }

  function onChipHover(e: CustomEvent<string | null>): void {
    highlightGroup = e.detail;
    revision += 1;
  }

  // Ctrl-click a chip: focus that color group + the direct neighbors of its nodes (a node counts
  // if ANY of its colors is this group), e.g. "External" shows every external reference + this paper.
  function focusOnGroup(group: string): void {
    if (focusIds && focusLabel === group) {
      clearFocus();
      return;
    }
    const seeds = new Set(
      (graph?.nodes ?? [])
        .filter((n) => referenceNodeGroups(n, colorBy).includes(group))
        .map((n) => n.id),
    );
    if (!seeds.size) return;
    focusIds = neighborhoodOf(seeds);
    focusLabel = group;
    revision += 1;
  }

  $: focusLabelText =
    focusIds && graph
      ? (graph.nodes.find((n) => n.id === focusLabel)?.label ?? focusLabel)
      : '';

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function renderChart(chart: any): void {
    if (!graph) return;
    // OR legend filtering (a node is hidden only when ALL its colors are hidden) combined with the
    // ctrl-click neighborhood focus; edges are kept only between still-visible nodes.
    const hidden = orHiddenIds(graph.nodes, (n) => referenceNodeGroups(n, colorBy), hiddenGroups);
    const nodes = graph.nodes.filter(
      (n) => !hidden.has(n.id) && (!focusIds || focusIds.has(n.id)),
    );
    const ids = new Set(nodes.map((n) => n.id));
    const g = {
      ...graph,
      nodes,
      edges: graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
    };
    chart.setOption(
      buildReferenceGraphOption(g, weights, $activeVizTheme, {
        yAxis,
        colorBy,
        showRefEdges: includeRefEdges,
        highlightGroups: highlightGroup ? new Set([highlightGroup]) : null,
      }),
      true,
    );
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
    // Node COLOR filtering/focus is the chip row (OR-aware); the native legend now carries only the
    // edge-class color key. Delegate clicks on the enterable-tooltip links (overlap clusters) below.
    // 'auxclick' catches middle-click (button 1); 'click' also fires with ctrl/meta held.
    chart.getDom()?.addEventListener('click', onContainerClick);
    chart.getDom()?.addEventListener('auxclick', onContainerClick);
    // 2026-07-16: live-filter the grouped-node tooltip's paper list (delegated, CSP-safe).
    chart.getDom()?.addEventListener('input', onTooltipSearch);
    chart.getDom()?.addEventListener('keydown', onTooltipKey);
    chart.getDom()?.addEventListener('keyup', onTooltipKey);
    wireEdgeSnapZoom(chart);
  }

  // Y-axis / colour / ref-edge-visibility changes are pure client-side restyles — bump the
  // revision, no refetch (2026-07-16).
  $: {
    yAxis;
    colorBy;
    includeRefEdges;
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
    hiddenGroups = new Set();
    soloGroup = null;
    highlightGroup = null;
    if (focusIds) clearFocus();
    else revision += 1;
  }

  function toggleRefEdges(): void {
    // Client-side only: the ref→ref edges are already in the payload (always fetched), so this just
    // flips their visibility via the revision reactive — no refetch, no view reset.
    includeRefEdges = !includeRefEdges;
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

<Modal title={baseTitle ? `Reference graph — ${baseTitle}` : 'Reference graph'} wide {onClose}>
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
        <option value="shelf">Shelf</option>
        <option value="rack">Rack</option>
        <option value="tag">Tag</option>
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
  <ColorGroupChips
    groups={refLegend.groups}
    colors={refLegend.colors}
    hidden={hiddenGroups}
    on:toggle={onChipToggle}
    on:hover={onChipHover}
  />
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
