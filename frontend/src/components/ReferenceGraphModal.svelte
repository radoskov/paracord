<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  import type { ApiClient, ReferenceGraph } from '../api/client';
  import Modal from './Modal.svelte';
  import { activeVizTheme } from '../lib/theme/store';
  import { pendingLibraryOpen } from '../lib/selection';
  import {
    DEFAULT_SECTION_WEIGHTS,
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
  let yAxis = 'weighted';

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
      graph = await client.referenceGraph(workId, { includeRefEdges });
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
        chart.on('click', (params: { data?: { node?: { resolved_work_id: string | null } } }) => {
          const id = params.data?.node?.resolved_work_id;
          if (id) {
            pendingLibraryOpen.set(id);
            if (typeof window !== 'undefined') window.location.hash = '#library';
            onClose();
          }
        });
      }
      chart.setOption(buildReferenceGraphOption(graph, weights, $activeVizTheme, { yAxis }), true);
      chartError = false;
    } catch {
      chartError = true;
    }
  }

  // Re-render on theme or Y-axis change (both are pure client-side restyles — no refetch).
  $: if (chart && graph && (yAxis || $activeVizTheme)) void render();

  function toggleRefEdges(): void {
    includeRefEdges = !includeRefEdges;
    void loadGraph();
  }

  onMount(async () => {
    await loadWeights();
    await loadGraph();
  });
  onDestroy(() => {
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
    <label title="Also draw citation links between the in-library references that cite each other">
      <input type="checkbox" checked={includeRefEdges} on:change={toggleRefEdges} data-testid="rg-ref-edges" />
      Local reference-to-reference edges
    </label>
    <span class="muted"
      >X = year · node size = section-weighted citations (weights in Profile) · the highlighted node
      is this paper.{#if naCount > 0}
        {naCount} reference{naCount === 1 ? '' : 's'} have no value for this axis (dashed, on the
        “n/a” lane).{/if}</span
    >
  </div>
  {#if message}<p class="msg" role="status">{message}</p>{/if}
  {#if loading && !graph}<p class="muted">Loading…</p>{/if}
  <div class="rg-chart" bind:this={container} data-testid="rg-chart"></div>
  {#if chartError}<p class="muted">Interactive chart unavailable in this environment.</p>{/if}
  {#if graph && graph.nodes.length <= 1}
    <p class="muted">This paper has no extracted references yet — run extraction to populate them.</p>
  {/if}
</Modal>

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
</style>
