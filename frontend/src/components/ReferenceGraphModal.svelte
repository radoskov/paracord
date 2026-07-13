<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  import type { ApiClient, ReferenceGraph, ReferenceGraphNode } from '../api/client';
  import Modal from './Modal.svelte';
  import { activeVizTheme } from '../lib/theme/store';
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
  let maxExternal = 50;
  let yAxis = 'weighted';
  let colorBy = 'kind';
  let showHelp = false;

  // How many reference nodes have no value for the current Y axis (→ the dashed "n/a" lane).
  $: naCount = graph
    ? graph.nodes.filter((n) => n.kind !== 'base' && yValueFor(n, yAxis, weights) == null).length
    : 0;
  let loading = true;
  let message = '';
  let container: HTMLDivElement | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let chart: any = null;
  let chartError = false;

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
    } catch {
      // keep defaults if preferences can't be read
    }
  }

  async function loadGraph(): Promise<void> {
    loading = true;
    message = '';
    try {
      graph = await client.referenceGraph(workId, { includeRefEdges, includeCiting, maxExternal });
      await render();
    } catch (error) {
      message = errorMessage(error);
    } finally {
      loading = false;
    }
  }

  async function render(): Promise<void> {
    if (!graph || !container) return;
    try {
      const echarts = (await import('echarts')) as unknown as {
        init: (el: HTMLElement) => typeof chart;
      };
      if (!chart) {
        chart = echarts.init(container);
        chart.on('click', (params: { data?: { node?: ReferenceGraphNode } }) => {
          const node = params.data?.node;
          if (node) openOrImportNode(node);
        });
        // Delegate clicks on the enterable-tooltip links (overlap clusters) at the container level.
        container.addEventListener('click', onContainerClick);
      }
      chart.setOption(
        buildReferenceGraphOption(graph, weights, $activeVizTheme, { yAxis, colorBy }),
        true,
      );
      chartError = false;
    } catch {
      chartError = true;
    }
  }

  // Re-render on theme, Y-axis, or colour change (all pure client-side restyles — no refetch).
  $: if (chart && graph && (yAxis || colorBy || $activeVizTheme)) void render();

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
  onDestroy(() => {
    if (container) container.removeEventListener('click', onContainerClick);
    if (chart) chart.dispose();
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
      <input type="number" min="0" max="500" bind:value={maxExternal} on:change={() => loadGraph()}
        class="max-external" data-testid="rg-max-external" />
    </label>
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
  <div class="rg-chart" bind:this={container} data-testid="rg-chart"></div>
  {#if chartError}<p class="muted">Interactive chart unavailable in this environment.</p>{/if}
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
