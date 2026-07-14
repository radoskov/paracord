<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type GraphScopeType,
    type VizPayload,
  } from '../api/client';
  import { getRenderer, registeredViewTypes } from '../lib/viz/registry';
  // Importing a renderer registers it in the registry (side-effect imports).
  import { restyleTemporalMap } from '../lib/viz/temporalMap';
  import '../lib/viz/embeddingCluster';
  import '../lib/viz/coCitation';
  import '../lib/viz/topicRiver';
  import '../lib/viz/similarityHeatmap';
  import { VIEW_HELP, axisOptionHelp, helpForView } from '../lib/viz/vizHelp';
  import Modal from '../components/Modal.svelte';
  import ChartHost from '../components/ChartHost.svelte';
  import ScopePicker from '../components/ScopePicker.svelte';
  import { activeVizTheme } from '../lib/theme/store';
  import { enableLegendSolo } from '../lib/viz/legendSolo';
  import { resolveScopeRequest } from '../lib/scope';
  import { pendingLibraryOpen, selectedPaperIds } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;
  // Whether the tab is visible (#9): ECharts mis-sizes when built while display:none, so resize
  // when the tab is shown again.
  export let visible = true;

  let viewTypes: string[] = registeredViewTypes();
  let viewType = viewTypes[0] ?? 'temporal_map';
  // Scope state, bound into the shared ScopePicker (C3); readiness is computed there.
  let scopeType: GraphScopeType = 'library';
  let scopeId = '';
  let searchQuery = '';
  let batchId = '';
  let savedFilterId = '';
  let scopeReady = true;

  let xAxis = 'year';
  let yAxis = 'local_degree';
  let sizeBy = 'local_degree';
  let colorBy = 'status';
  let edgeContext = 'coupling';
  // B3: citation edges are shown by default (the point of the map is inter-connectedness); above
  // edgeMaxNodes papers they're suppressed server-side with a note (raise the limit to force them).
  let includeEdges = true;
  let edgeMaxNodes = 150;
  let focusWorkId = '';
  // embedding_cluster projection (P5b): PCA default | UMAP opt-in (needs the AI extra image).
  let clusterLayout = 'pca';

  const EDGE_CONTEXT_OPTIONS = [
    ['coupling', 'Bibliographic coupling (shared references)'],
    ['co_citation', 'Co-citation (cited together)'],
  ];

  let payload: VizPayload | null = null;
  let busy = false;
  let message = '';

  let chartRevision = 0;

  const SIZE_OPTIONS = [
    ['local_degree', 'Local citation degree'],
    ['citation_count', 'Citation count'],
    ['year', 'Publication year'], // 5j
    ['none', 'Uniform'],
  ];
  const COLOR_OPTIONS = [
    ['status', 'Reading status'],
    ['work_type', 'Work type'],
    ['year', 'Publication year'], // 5h (one colour per distinct year)
    ['venue', 'Venue'], // 5d
    ['none', 'None'],
  ];

  $: axisOptions = payload?.axis_options ?? [
    { key: 'year', label: 'Publication year' },
    { key: 'citation_count', label: 'Citation count' },
    { key: 'local_degree', label: 'Local citation degree' },
    { key: 'citation_velocity', label: 'Citation velocity' },
    { key: 'similarity_to_focus', label: 'Similarity to focus' },
    { key: 'topic_similarity_to_focus', label: 'Topic similarity to focus' },
    { key: 'keyword_similarity_to_focus', label: 'Keyword similarity to focus' }, // 5b
  ];

  // 5f: optional manual axis view-range (min/max) overriding ECharts auto-range. Empty = auto. A
  // corrupt outlier (e.g. year 2695) no longer stretches the whole plot once a max is set.
  let xMin = '';
  let xMax = '';
  let yMin = '';
  let yMax = '';
  const _num = (s: string): number | undefined => {
    const n = Number(s);
    return s.trim() !== '' && Number.isFinite(n) ? n : undefined;
  };

  // The embedding-cluster view has fixed PCA-component axes and server-driven cluster coloring, so
  // the axis / color / edge controls do not apply — only the size encoding and node cap do.
  $: isCluster = viewType === 'embedding_cluster';
  // The temporal map is the only view with swappable axes / size / color / focus / edge overlay.
  $: isTemporal = viewType === 'temporal_map';
  // Co-citation is a node-link network (color + edge-context controls, no axes/size).
  $: isNetwork = viewType === 'co_citation';
  // Topic river / similarity heatmap are chart views with no per-view controls.
  $: isChart = viewType === 'topic_river' || viewType === 'similarity_heatmap';

  $: needsFocus = isTemporal && (xAxis.endsWith('_to_focus') || yAxis.endsWith('_to_focus'));

  onMount(async () => {
    try {
      viewTypes = await client.listVizViewTypes().catch(() => registeredViewTypes());
    } catch (error) {
      message = errorMessage(error);
    }
  });

  async function load(): Promise<void> {
    if (!scopeReady) return;
    busy = true;
    message = '';
    try {
      const scopeArgs = await resolveScopeRequest(
        client,
        { scopeType, scopeId, searchQuery, batchId, savedFilterId },
        $selectedPaperIds,
      );
      payload = await client.visualization(viewType, {
        ...scopeArgs,
        xAxis,
        yAxis,
        sizeBy,
        colorBy,
        edgeContext,
        focusWorkId: focusWorkId || null,
        includeEdges,
        edgeMaxNodes,
        layout: isCluster ? clusterLayout : undefined,
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

  // C4: on the temporal map, size/color are re-encodings of metrics already in each node's meta —
  // restyle the loaded payload client-side (no server round-trip). Other views still refetch
  // (their size/color changes the server-side computation).
  function restyleIfLoaded(): void {
    if (!payload) return;
    if (payload.view_type !== 'temporal_map') {
      void load();
      return;
    }
    payload = restyleTemporalMap(payload, sizeBy, colorBy);
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function renderChart(chart: any): void {
    if (!payload) return;
    const renderer = getRenderer(payload.view_type);
    if (!renderer) throw new Error("no renderer");
    chart.setOption(renderer.buildOption(payload, $activeVizTheme), true);
    // 5f: apply the manual axis view-range on top of the built option (temporal map only — the
    // scatter view with value axes). A merge setOption keeps everything else; `null` = auto.
    if (isTemporal) {
      chart.setOption({
        xAxis: { min: _num(xMin) ?? null, max: _num(xMax) ?? null },
        yAxis: { min: _num(yMin) ?? null, max: _num(yMax) ?? null },
      });
    }
    chart.off('click');
    chart.on('click', (params: { data?: { name?: string } }) => {
      if (params.data?.name) openPaper(params.data.name);
    });
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function wireEvents(chart: any): void {
    // Delegate clicks on an overlap tooltip's per-paper [open] links (B4). The tooltip is
    // enterable, so the user can move into it; the links carry the work id.
    chart.getDom()?.addEventListener('click', (e: Event) => {
      const el = (e.target as HTMLElement | null)?.closest?.('[data-viz-open]');
      const id = el?.getAttribute('data-viz-open');
      if (id) openPaper(id);
    });
    // Shift-click a legend entry to show only that group; shift-click again to show all.
    enableLegendSolo(chart);
  }

  // B1 help: description of the current view + the "About this view" / "Visualization types" popups.
  let showAbout = false;
  let showTypes = false;
  $: help = helpForView(viewType);
  const allViewHelp = Object.values(VIEW_HELP);

  // Open a paper in the Library tab (reused by the click handler + the B2 "needs a PDF" list).
  function openPaper(workId: string): void {
    pendingLibraryOpen.set(workId);
    if (typeof window !== 'undefined') window.location.hash = '#library';
  }

  // A payload has something to draw if it has nodes (scatter/network) or a chart carrier
  // (topic river's series / similarity heatmap's matrix).
  $: hasData =
    !!payload &&
    (payload.nodes.length > 0 ||
      (payload.series?.years.length ?? 0) > 0 ||
      (payload.matrix?.labels.length ?? 0) > 0);

  $: if (payload && hasData) chartRevision += 1;
</script>

<section class="layout">
  {#if message}<p class="msg" role="status">{message}</p>{/if}

  <div class="card">
    <div class="viz-header">
      <h2>Visualizations</h2>
      <button
        type="button"
        class="secondary"
        data-testid="viz-types-help"
        on:click={() => (showTypes = true)}
        title="Overview of every visualization type and what each is for"
      >
        Visualization types
      </button>
    </div>
    <p class="muted">Explore your library visually — pick a view below and Build it over a scope.</p>

    <div class="controls">
      <label>
        View
        <select bind:value={viewType} on:change={reloadIfLoaded} data-testid="viz-view-select" title="The visualization type — see ‘Visualization types’ for what each does">
          {#each viewTypes as vt (vt)}<option value={vt}>{vt.replace('_', ' ')}</option>{/each}
        </select>
      </label>
    </div>
    {#if help}
      <p class="hintline" data-testid="viz-view-desc">
        {help.short}
        <button type="button" class="link" on:click={() => (showAbout = true)}
          title="More about this view and its settings">ⓘ About this view</button>
      </p>
    {/if}

    <div class="controls">
      <ScopePicker
        {client}
        bind:scopeType
        bind:scopeId
        bind:searchQuery
        bind:batchId
        bind:savedFilterId
        bind:ready={scopeReady}
        verb="visualize"
        testid="viz"
      />
    </div>

    <div class="controls">
      {#if isTemporal}
        <label>X axis
          <select bind:value={xAxis} on:change={reloadIfLoaded} data-testid="viz-x-axis" title="The value plotted on the horizontal axis — year, citation count, local degree, velocity, or similarity to a focus paper">
            {#each axisOptions as opt (opt.key)}<option value={opt.key}>{opt.label}</option>{/each}
          </select>
        </label>
        <label>Y axis
          <select bind:value={yAxis} on:change={reloadIfLoaded} data-testid="viz-y-axis" title="The value plotted on the vertical axis — year, citation count, local degree, velocity, or similarity to a focus paper">
            {#each axisOptions as opt (opt.key)}<option value={opt.key}>{opt.label}</option>{/each}
          </select>
        </label>
        <!-- 5f: manual view-range so a bad outlier (e.g. year 2695) can't stretch the plot. Empty = auto. -->
        <label class="range">X range
          <input type="number" bind:value={xMin} on:change={() => void render()} placeholder="min"
            data-testid="viz-x-min" title="Minimum X shown (blank = auto)" />
          <input type="number" bind:value={xMax} on:change={() => void render()} placeholder="max"
            data-testid="viz-x-max" title="Maximum X shown (blank = auto)" />
        </label>
        <label class="range">Y range
          <input type="number" bind:value={yMin} on:change={() => void render()} placeholder="min"
            data-testid="viz-y-min" title="Minimum Y shown (blank = auto)" />
          <input type="number" bind:value={yMax} on:change={() => void render()} placeholder="max"
            data-testid="viz-y-max" title="Maximum Y shown (blank = auto)" />
        </label>
      {:else if isCluster}
        <label>Layout
          <select bind:value={clusterLayout} on:change={reloadIfLoaded} data-testid="viz-cluster-layout"
            title="2D projection: PCA (built-in) or UMAP (needs the AI extra image)">
            <option value="pca">PCA (built-in)</option>
            <option value="umap">UMAP (AI extra)</option>
          </select>
        </label>
        <span class="hintline" data-testid="viz-cluster-hint">
          {#if clusterLayout === 'umap' && payload && payload.legend?.layout === 'pca'}
            UMAP needs the opt-in AI extra image; showing PCA.
          {:else}
            Papers are placed by embedding proximity; color shows the topic cluster.
          {/if}
        </span>
      {:else if isNetwork}
        <label>Edge
          <select bind:value={edgeContext} on:change={reloadIfLoaded} data-testid="viz-edge-context" title="How two papers are linked">
            {#each EDGE_CONTEXT_OPTIONS as [value, label] (value)}<option {value}>{label}</option>{/each}
          </select>
        </label>
      {:else if viewType === 'topic_river'}
        <span class="hintline" data-testid="viz-chart-hint">
          Share of papers in each topic across publication years (topics from embedding clusters).
        </span>
      {:else if viewType === 'similarity_heatmap'}
        <span class="hintline" data-testid="viz-chart-hint">
          Pairwise cosine similarity for up to 50 papers (most recent kept for larger scopes).
        </span>
      {/if}
      {#if isTemporal || isCluster}
        <label>Size
          <select bind:value={sizeBy} on:change={restyleIfLoaded} data-testid="viz-size-by" title="What point size represents — local citation degree or citation count">
            {#each SIZE_OPTIONS as [value, label] (value)}<option {value}>{label}</option>{/each}
          </select>
        </label>
      {/if}
      {#if isTemporal || isNetwork}
        <label>Color
          <select bind:value={colorBy} on:change={restyleIfLoaded} data-testid="viz-color-by" title="What point colour represents — e.g. reading status">
            {#each COLOR_OPTIONS as [value, label] (value)}<option {value}>{label}</option>{/each}
          </select>
        </label>
      {/if}
      {#if needsFocus}
        <label>Focus paper
          <select bind:value={focusWorkId} on:change={reloadIfLoaded} data-testid="viz-focus" title="The focus paper the ‘similarity to focus’ axes compare every other paper against">
            <option value="">Choose a focus paper…</option>
            {#each payload?.nodes ?? [] as node (node.id)}<option value={node.id}>{node.label}</option>{/each}
          </select>
        </label>
      {/if}
      {#if isTemporal}
        <label class="toggle" title="Overlay citation links among the papers (shown by default)">
          <input type="checkbox" bind:checked={includeEdges} on:change={reloadIfLoaded} data-testid="viz-include-edges" />
          Citation edges
        </label>
        {#if includeEdges}
          <label title="Above this many papers the citation edges are hidden to keep the map readable; raise it to force them, then Build/Refresh">
            Edge limit
            <input
              type="number"
              min="1"
              max="500"
              bind:value={edgeMaxNodes}
              data-testid="viz-edge-max-nodes"
            />
          </label>
        {/if}
      {/if}
      <button type="button" on:click={load} disabled={busy || !scopeReady} data-testid="viz-build">
        {payload ? 'Refresh' : 'Build'}
      </button>
    </div>

  </div>

  {#if payload}
    <div class="card">
      {#if payload.notes.length > 0}
        <ul class="notes" data-testid="viz-notes">
          {#each payload.notes as note (note)}<li>{note}</li>{/each}
        </ul>
      {/if}
      {#if payload.reindex_hint}
        <div class="notes" data-testid="viz-reindex-hint">
          {#if payload.reindex_hint.reindexable > 0}
            <p>
              ⚠ {payload.reindex_hint.reindexable} paper{payload.reindex_hint.reindexable === 1
                ? ''
                : 's'} aren't indexed for this model — reindex embeddings (AI &amp; Models) to include
              them.
            </p>
          {/if}
          {#if payload.reindex_hint.needs_text.length > 0}
            <details data-testid="viz-needs-text">
              <summary
                >⚠ {payload.reindex_hint.needs_text.length} paper{payload.reindex_hint.needs_text
                  .length === 1
                  ? ' has'
                  : 's have'} no extracted text yet — attach a PDF and extract (reindexing can't include
                {payload.reindex_hint.needs_text.length === 1 ? 'it' : 'them'})</summary
              >
              <ul>
                {#each payload.reindex_hint.needs_text as p (p.work_id)}
                  <li>
                    <button type="button" class="link" on:click={() => openPaper(p.work_id)}
                      title="Open this paper to attach a PDF and extract it">{p.title}</button>
                  </li>
                {/each}
              </ul>
            </details>
          {/if}
        </div>
      {/if}
      {#if !hasData}
        <p class="empty">No papers to plot in this scope.</p>
      {:else}
        <div class="chart" data-testid="viz-chart">
          <ChartHost render={renderChart} onReady={wireEvents} revision={chartRevision} {visible}
            height="100%" ariaLabel="Visualization chart">
            <svelte:fragment slot="fallback">Interactive chart unavailable in this environment.</svelte:fragment>
          </ChartHost>
        </div>
        <p class="hint">Hover for details · click a point to open the paper.</p>
      {/if}
    </div>
  {/if}
</section>

{#if showAbout}
  <Modal title={`About: ${help.name}`} onClose={() => (showAbout = false)}>
    <p>{help.about}</p>
    {#if help.requirements}
      <p class="muted"><strong>Requirements:</strong> {help.requirements}</p>
    {/if}
    {#if help.params.length}
      <h4>Settings</h4>
      <dl class="viz-params">
        {#each help.params as p (p.name)}
          <dt>{p.name}</dt>
          <dd>{p.help}</dd>
        {/each}
      </dl>
    {/if}
    {#if isTemporal}
      <h4>How to read each axis</h4>
      <p class="muted">Any of these can go on the X or Y axis.</p>
      <dl class="viz-params" data-testid="viz-axis-help">
        {#each axisOptions as opt (opt.key)}
          <dt>{opt.label}</dt>
          <dd>{axisOptionHelp(opt.key)}</dd>
        {/each}
      </dl>
    {/if}
  </Modal>
{/if}

{#if showTypes}
  <Modal title="Visualization types" wide onClose={() => (showTypes = false)}>
    <p class="muted">Pick the view that matches what you want to see.</p>
    {#each allViewHelp as v (v.key)}
      <div class="viz-type-entry">
        <h4>{v.name}</h4>
        <p>{v.short}</p>
        {#if v.requirements}
          <p class="muted"><strong>Requirements:</strong> {v.requirements}</p>
        {/if}
      </div>
    {/each}
  </Modal>
{/if}

<style>
  .layout {
    display: grid;
    gap: 1rem;
  }

  .card {
    background: var(--surface-raised);
    border: 1px solid var(--border-strong);
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
    color: var(--ink-muted);
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
    color: var(--ink-strong);
    display: flex;
    flex-direction: column;
    font-size: 0.8rem;
    font-weight: 700;
    gap: 0.2rem;
  }

  /* 5f: paired min/max number inputs sit side by side, each compact. */
  .range {
    flex-direction: row;
    align-items: center;
    gap: 0.3rem;
  }

  .range input {
    width: 4.5rem;
  }

  .toggle {
    flex-direction: row;
    align-items: center;
    gap: 0.35rem;
  }

  select,
  input {
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    font: inherit;
    font-weight: 400;
    padding: 0.3rem 0.5rem;
  }

  button {
    background: var(--accent-primary);
    border: 1px solid var(--accent-primary);
    border-radius: 6px;
    color: var(--ink-inverse);
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

  .chart {
    height: min(64vh, 36rem);
    width: 100%;
  }

  .notes {
    background: var(--status-warning-bg);
    border-radius: 6px;
    color: var(--status-warning);
    font-size: 0.82rem;
    list-style: none;
    margin: 0 0 0.6rem;
    padding: 0.4rem 0.7rem;
  }

  .notes li {
    margin: 0.1rem 0;
  }

  .empty {
    color: var(--ink-muted);
  }

  .viz-header {
    align-items: center;
    display: flex;
    gap: 0.75rem;
    justify-content: space-between;
  }

  .link {
    background: none;
    border: none;
    color: var(--accent, #3b82f6);
    cursor: pointer;
    font: inherit;
    padding: 0;
    text-decoration: underline;
  }

  .viz-params {
    display: grid;
    gap: 0.15rem 0.75rem;
    grid-template-columns: max-content 1fr;
    margin: 0.3rem 0 0;
  }

  .viz-params dt {
    font-weight: 600;
  }

  .viz-params dd {
    color: var(--ink-muted);
    margin: 0;
  }

  .viz-type-entry {
    border-top: 1px solid var(--border, #e5e7eb);
    margin-top: 0.6rem;
    padding-top: 0.6rem;
  }

  .viz-type-entry h4 {
    margin: 0 0 0.2rem;
  }
</style>
