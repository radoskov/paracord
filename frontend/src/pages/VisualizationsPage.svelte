<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  import {
    ApiClient,
    type GraphScopeType,
    type Rack,
    type Shelf,
    type VizPayload,
  } from '../api/client';
  import { getRenderer, registeredViewTypes } from '../lib/viz/registry';
  // Importing a renderer registers it in the registry (side-effect imports).
  import '../lib/viz/temporalMap';
  import '../lib/viz/embeddingCluster';
  import { resolveTheme } from '../lib/viz/theme';
  import { pendingLibraryOpen, selectedPaperIds } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;
  // Whether the tab is visible (#9): ECharts mis-sizes when built while display:none, so resize
  // when the tab is shown again.
  export let visible = true;

  let viewTypes: string[] = registeredViewTypes();
  let viewType = viewTypes[0] ?? 'temporal_map';
  let scopeType: GraphScopeType = 'library';
  let scopeId = '';
  let searchQuery = '';
  let shelves: Shelf[] = [];
  let racks: Rack[] = [];

  let xAxis = 'year';
  let yAxis = 'local_degree';
  let sizeBy = 'local_degree';
  let colorBy = 'status';
  let includeEdges = false;
  let focusWorkId = '';

  let payload: VizPayload | null = null;
  let busy = false;
  let message = '';

  let chartContainer: HTMLDivElement | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let chart: any = null;
  let chartError = false;

  const SIZE_OPTIONS = [
    ['local_degree', 'Local citation degree'],
    ['citation_count', 'Citation count'],
    ['none', 'Uniform'],
  ];
  const COLOR_OPTIONS = [
    ['status', 'Reading status'],
    ['work_type', 'Work type'],
    ['none', 'None'],
  ];

  $: axisOptions = payload?.axis_options ?? [
    { key: 'year', label: 'Publication year' },
    { key: 'citation_count', label: 'Citation count' },
    { key: 'local_degree', label: 'Local citation degree' },
    { key: 'citation_velocity', label: 'Citation velocity' },
    { key: 'similarity_to_focus', label: 'Similarity to focus' },
    { key: 'topic_similarity_to_focus', label: 'Topic similarity to focus' },
  ];

  // The embedding-cluster view has fixed PCA-component axes and server-driven cluster coloring, so
  // the axis / color / edge controls do not apply — only the size encoding and node cap do.
  $: isCluster = viewType === 'embedding_cluster';

  $: needsFocus = !isCluster && (xAxis.endsWith('_to_focus') || yAxis.endsWith('_to_focus'));

  $: scopeReady =
    scopeType === 'library'
      ? true
      : scopeType === 'shelf' || scopeType === 'rack'
        ? !!scopeId
        : scopeType === 'search_result'
          ? !!searchQuery.trim()
          : scopeType === 'selected_papers'
            ? $selectedPaperIds.length > 0
            : false;

  onMount(async () => {
    try {
      [shelves, racks, viewTypes] = await Promise.all([
        client.listShelves(),
        client.listRacks(),
        client.listVizViewTypes().catch(() => registeredViewTypes()),
      ]);
    } catch (error) {
      message = errorMessage(error);
    }
  });

  async function load(): Promise<void> {
    if (!scopeReady) return;
    busy = true;
    message = '';
    try {
      let workIds: string[] | undefined;
      if (scopeType === 'search_result') {
        const works = (await client.listWorks({ q: searchQuery, perPage: 500 })).items;
        workIds = works.map((w) => w.id);
      } else if (scopeType === 'selected_papers') {
        workIds = $selectedPaperIds;
      }
      payload = await client.visualization(viewType, {
        scopeType,
        scopeId: scopeType === 'shelf' || scopeType === 'rack' ? scopeId : null,
        workIds,
        xAxis,
        yAxis,
        sizeBy,
        colorBy,
        focusWorkId: focusWorkId || null,
        includeEdges,
      });
    } catch (error) {
      message = errorMessage(error);
      payload = null;
    } finally {
      busy = false;
    }
  }

  // Changing an axis/encoding re-fetches once a payload already exists (the controls are live);
  // before the first build the controls just stage the request. Called from each control's change
  // handler rather than a reactive block, so it can never self-trigger a fetch loop.
  function reloadIfLoaded(): void {
    if (payload) void load();
  }

  async function render(): Promise<void> {
    if (!payload || !chartContainer) return;
    const renderer = getRenderer(payload.view_type);
    if (!renderer) {
      chartError = true;
      return;
    }
    try {
      const echarts = (await import('echarts')) as unknown as {
        init: (el: HTMLElement) => typeof chart;
      };
      if (!chart) chart = echarts.init(chartContainer);
      const theme = resolveTheme(
        typeof document !== 'undefined' &&
          document.documentElement.getAttribute('data-theme') === 'dark'
          ? 'dark'
          : 'light',
      );
      chart.setOption(renderer.buildOption(payload, theme), true);
      chart.off('click');
      chart.on('click', (params: { data?: { name?: string } }) => {
        const workId = params.data?.name;
        if (workId) {
          pendingLibraryOpen.set(workId);
          if (typeof window !== 'undefined') window.location.hash = '#library';
        }
      });
      chartError = false;
    } catch {
      chartError = true;
    }
  }

  $: if (payload && chartContainer) void render();

  let wasVisible = true;
  $: {
    if (visible && !wasVisible && chart) chart.resize();
    wasVisible = visible;
  }

  onDestroy(() => {
    if (chart) chart.dispose();
  });
</script>

<section class="layout">
  {#if message}<p class="msg" role="status">{message}</p>{/if}

  <div class="card">
    <h2>Visualizations</h2>
    <p class="muted">
      Explore your library visually. The <strong>temporal citation map</strong> plots each paper as
      a point; pick any value for each axis.
    </p>

    <div class="controls">
      <label>
        View
        <select bind:value={viewType} on:change={reloadIfLoaded} data-testid="viz-view-select" title="Visualization type">
          {#each viewTypes as vt (vt)}<option value={vt}>{vt.replace('_', ' ')}</option>{/each}
        </select>
      </label>

      <label>
        Scope
        <select bind:value={scopeType} data-testid="viz-scope-type" title="What to visualize">
          <option value="library">Whole library</option>
          <option value="shelf">A shelf</option>
          <option value="rack">A rack</option>
          <option value="search_result">Search result</option>
          <option value="selected_papers">Selected papers</option>
        </select>
      </label>

      {#if scopeType === 'shelf'}
        <label>Shelf
          <select bind:value={scopeId} data-testid="viz-scope-id">
            <option value="">Choose a shelf…</option>
            {#each shelves as shelf (shelf.id)}<option value={shelf.id}>{shelf.name}</option>{/each}
          </select>
        </label>
      {:else if scopeType === 'rack'}
        <label>Rack
          <select bind:value={scopeId} data-testid="viz-scope-id">
            <option value="">Choose a rack…</option>
            {#each racks as rack (rack.id)}<option value={rack.id}>{rack.name}</option>{/each}
          </select>
        </label>
      {:else if scopeType === 'search_result'}
        <label>Query
          <input bind:value={searchQuery} placeholder="Search papers…" data-testid="viz-scope-search" />
        </label>
      {:else if scopeType === 'selected_papers'}
        <span class="selcount">{$selectedPaperIds.length} selected</span>
      {/if}
    </div>

    <div class="controls">
      {#if !isCluster}
        <label>X axis
          <select bind:value={xAxis} on:change={reloadIfLoaded} data-testid="viz-x-axis" title="Value on the X axis">
            {#each axisOptions as opt (opt.key)}<option value={opt.key}>{opt.label}</option>{/each}
          </select>
        </label>
        <label>Y axis
          <select bind:value={yAxis} on:change={reloadIfLoaded} data-testid="viz-y-axis" title="Value on the Y axis">
            {#each axisOptions as opt (opt.key)}<option value={opt.key}>{opt.label}</option>{/each}
          </select>
        </label>
      {:else}
        <span class="hintline" data-testid="viz-cluster-hint">
          Papers are placed by embedding proximity (PCA); color shows the topic cluster.
        </span>
      {/if}
      <label>Size
        <select bind:value={sizeBy} on:change={reloadIfLoaded} data-testid="viz-size-by" title="Point size encoding">
          {#each SIZE_OPTIONS as [value, label] (value)}<option {value}>{label}</option>{/each}
        </select>
      </label>
      {#if !isCluster}
        <label>Color
          <select bind:value={colorBy} on:change={reloadIfLoaded} data-testid="viz-color-by" title="Point color encoding">
            {#each COLOR_OPTIONS as [value, label] (value)}<option {value}>{label}</option>{/each}
          </select>
        </label>
      {/if}
      {#if needsFocus}
        <label>Focus paper
          <select bind:value={focusWorkId} on:change={reloadIfLoaded} data-testid="viz-focus" title="Reference paper for similarity">
            <option value="">Choose a focus paper…</option>
            {#each payload?.nodes ?? [] as node (node.id)}<option value={node.id}>{node.label}</option>{/each}
          </select>
        </label>
      {/if}
      {#if !isCluster}
        <label class="toggle" title="Overlay citation links among the papers">
          <input type="checkbox" bind:checked={includeEdges} on:change={reloadIfLoaded} data-testid="viz-include-edges" />
          Citation edges
        </label>
      {/if}
      <button type="button" on:click={load} disabled={busy || !scopeReady} data-testid="viz-build">
        {payload ? 'Refresh' : 'Build'}
      </button>
    </div>

    {#if !scopeReady}
      <p class="hintline">
        {#if scopeType === 'selected_papers'}Select papers in the Library tab first.
        {:else if scopeType === 'search_result'}Type a search to visualize its results.
        {:else}Pick a {scopeType} to visualize.{/if}
      </p>
    {/if}
  </div>

  {#if payload}
    <div class="card">
      {#if payload.notes.length > 0}
        <ul class="notes" data-testid="viz-notes">
          {#each payload.notes as note (note)}<li>{note}</li>{/each}
        </ul>
      {/if}
      {#if payload.nodes.length === 0}
        <p class="empty">No papers to plot in this scope.</p>
      {:else}
        <div class="chart" bind:this={chartContainer} data-testid="viz-chart"></div>
        {#if chartError}
          <p class="empty">Interactive chart unavailable in this environment.</p>
        {:else}
          <p class="hint">Hover for details · click a point to open the paper.</p>
        {/if}
      {/if}
    </div>
  {/if}
</section>

<style>
  .layout {
    display: grid;
    gap: 1rem;
  }

  .card {
    background: #fff;
    border: 1px solid #d8dee6;
    border-radius: 8px;
    padding: 1rem;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0 0 0.4rem;
  }

  .muted,
  .hintline,
  .hint {
    color: #64717f;
    font-size: 0.85rem;
  }

  .controls {
    align-items: flex-end;
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    margin-top: 0.6rem;
  }

  label {
    color: #21303d;
    display: flex;
    flex-direction: column;
    font-size: 0.8rem;
    font-weight: 700;
    gap: 0.2rem;
  }

  .toggle {
    flex-direction: row;
    align-items: center;
    gap: 0.35rem;
  }

  select,
  input {
    border: 1px solid #bcc7d2;
    border-radius: 6px;
    font: inherit;
    font-weight: 400;
    padding: 0.3rem 0.5rem;
  }

  button {
    background: #203142;
    border: 1px solid #203142;
    border-radius: 6px;
    color: #fff;
    cursor: pointer;
    font: inherit;
    font-weight: 700;
    min-height: 2.1rem;
    padding: 0.35rem 0.8rem;
  }

  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .selcount {
    align-self: center;
    color: #64717f;
    font-size: 0.9rem;
  }

  .chart {
    height: min(64vh, 36rem);
    width: 100%;
  }

  .notes {
    background: #fef3c7;
    border-radius: 6px;
    color: #78350f;
    font-size: 0.82rem;
    list-style: none;
    margin: 0 0 0.6rem;
    padding: 0.4rem 0.7rem;
  }

  .notes li {
    margin: 0.1rem 0;
  }

  .empty {
    color: #64717f;
  }
</style>
