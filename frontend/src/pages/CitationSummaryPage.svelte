<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  import {
    ApiClient,
    type CitationSummary,
    type GraphScopeType,
    type MissingWork,
    type Rack,
    type RankedWork,
    type Shelf,
  } from '../api/client';
  import { buildChronologicalOption } from '../lib/viz/citationSummary';
  import { resolveTheme } from '../lib/viz/theme';
  import { pendingLibraryOpen, selectedPaperIds } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;
  // Whether the tab is visible (#9): ECharts mis-sizes when built while display:none.
  export let visible = true;

  let scopeType: GraphScopeType = 'library';
  let scopeId = '';
  let searchQuery = '';
  let shelves: Shelf[] = [];
  let racks: Rack[] = [];

  let summary: CitationSummary | null = null;
  let busy = false;
  let message = '';
  let importing = '';

  let chartContainer: HTMLDivElement | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let chart: any = null;
  let chartError = false;

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
      [shelves, racks] = await Promise.all([client.listShelves(), client.listRacks()]);
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
      summary = await client.citationSummary({
        scopeType,
        scopeId: scopeType === 'shelf' || scopeType === 'rack' ? scopeId : null,
        workIds,
      });
    } catch (error) {
      message = errorMessage(error);
      summary = null;
    } finally {
      busy = false;
    }
  }

  function openPaper(workId: string): void {
    pendingLibraryOpen.set(workId);
    if (typeof window !== 'undefined') window.location.hash = '#library';
  }

  async function importMissing(missing: MissingWork): Promise<void> {
    if (!missing.reference_id) return;
    importing = missing.key;
    try {
      const work = await client.importReferenceAsWork(missing.reference_id);
      // Refresh so the imported work leaves the "missing" list; then open it.
      await load();
      openPaper(work.id);
    } catch (error) {
      message = errorMessage(error);
    } finally {
      importing = '';
    }
  }

  async function render(): Promise<void> {
    if (!summary || !chartContainer || summary.chronological.length === 0) return;
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
      chart.setOption(buildChronologicalOption(summary, theme), true);
      chartError = false;
    } catch {
      chartError = true;
    }
  }

  $: if (summary && chartContainer) void render();

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
    <h2>Citation summary</h2>
    <p class="muted">
      Scoped citation analytics (§8.11): the most-cited papers in and beyond your library, the papers
      you cite most but don't have, bridge and isolated papers, and how your scope spreads over time.
    </p>

    <div class="controls">
      <label>
        Scope
        <select bind:value={scopeType} data-testid="summary-scope-type" title="What to summarize">
          <option value="library">Whole library</option>
          <option value="shelf">A shelf</option>
          <option value="rack">A rack</option>
          <option value="search_result">Search result</option>
          <option value="selected_papers">Selected papers</option>
        </select>
      </label>

      {#if scopeType === 'shelf'}
        <label>Shelf
          <select bind:value={scopeId} data-testid="summary-scope-id">
            <option value="">Choose a shelf…</option>
            {#each shelves as shelf (shelf.id)}<option value={shelf.id}>{shelf.name}</option>{/each}
          </select>
        </label>
      {:else if scopeType === 'rack'}
        <label>Rack
          <select bind:value={scopeId} data-testid="summary-scope-id">
            <option value="">Choose a rack…</option>
            {#each racks as rack (rack.id)}<option value={rack.id}>{rack.name}</option>{/each}
          </select>
        </label>
      {:else if scopeType === 'search_result'}
        <label>Query
          <input bind:value={searchQuery} placeholder="Search papers…" data-testid="summary-scope-search" />
        </label>
      {:else if scopeType === 'selected_papers'}
        <span class="selcount">{$selectedPaperIds.length} selected</span>
      {/if}

      <button type="button" on:click={load} disabled={busy || !scopeReady} data-testid="summary-build">
        {summary ? 'Refresh' : 'Summarize'}
      </button>
    </div>

    {#if !scopeReady}
      <p class="hintline">
        {#if scopeType === 'selected_papers'}Select papers in the Library tab first.
        {:else if scopeType === 'search_result'}Type a search to summarize its results.
        {:else}Pick a {scopeType} to summarize.{/if}
      </p>
    {/if}
  </div>

  {#if summary}
    <div class="card">
      {#if summary.notes.length > 0}
        <ul class="notes" data-testid="summary-notes">
          {#each summary.notes as note (note)}<li>{note}</li>{/each}
        </ul>
      {/if}
      <p class="meta" data-testid="summary-meta">
        {summary.scope_work_count} papers · bridge method: {summary.bridge_method}
      </p>

      <div class="grid">
        <div class="block" data-testid="summary-most-cited-local">
          <h3>Most-cited (in your library)</h3>
          {#if summary.most_cited_local.length === 0}
            <p class="empty">No local citations in this scope.</p>
          {:else}
            <ol>
              {#each summary.most_cited_local as w (w.work_id)}
                <li>
                  <button class="link" type="button" on:click={() => openPaper(w.work_id)}>{w.title}</button>
                  <span class="badge">{w.score} citing</span>
                </li>
              {/each}
            </ol>
          {/if}
        </div>

        <div class="block" data-testid="summary-most-cited-external">
          <h3>Most-cited (external impact)</h3>
          {#if summary.most_cited_external.length === 0}
            <p class="empty">No citation counts fetched for this scope yet.</p>
          {:else}
            <ol>
              {#each summary.most_cited_external as w (w.work_id)}
                <li>
                  <button class="link" type="button" on:click={() => openPaper(w.work_id)}>{w.title}</button>
                  <span class="badge">{w.score} citations</span>
                </li>
              {/each}
            </ol>
          {/if}
        </div>

        <div class="block" data-testid="summary-missing">
          <h3>Frequently cited but missing</h3>
          {#if summary.frequently_cited_missing.length === 0}
            <p class="empty">Every frequently-cited work is already in your library.</p>
          {:else}
            <ol>
              {#each summary.frequently_cited_missing as m (m.key)}
                <li>
                  <span class="missing-title">{m.title}</span>
                  <span class="badge">{m.cited_by_count} cite this</span>
                  {#if m.reference_id}
                    <button
                      class="import"
                      type="button"
                      disabled={importing === m.key}
                      on:click={() => importMissing(m)}
                      data-testid="summary-import"
                    >
                      {importing === m.key ? 'Importing…' : 'Import'}
                    </button>
                  {/if}
                </li>
              {/each}
            </ol>
          {/if}
        </div>

        <div class="block" data-testid="summary-bridge">
          <h3>Bridge papers</h3>
          {#if summary.bridge_papers.length === 0}
            <p class="empty">No bridge papers detected.</p>
          {:else}
            <ol>
              {#each summary.bridge_papers as w (w.work_id)}
                <li>
                  <button class="link" type="button" on:click={() => openPaper(w.work_id)}>{w.title}</button>
                  <span class="badge">centrality {w.score}</span>
                </li>
              {/each}
            </ol>
          {/if}
        </div>

        <div class="block" data-testid="summary-isolated">
          <h3>Isolated papers</h3>
          {#if summary.isolated_papers.length === 0}
            <p class="empty">Every paper connects to the rest of the scope.</p>
          {:else}
            <ol>
              {#each summary.isolated_papers as w (w.work_id)}
                <li>
                  <button class="link" type="button" on:click={() => openPaper(w.work_id)}>{w.title}</button>
                </li>
              {/each}
            </ol>
          {/if}
        </div>

        <div class="block chrono" data-testid="summary-chronological">
          <h3>Papers by year</h3>
          {#if summary.chronological.length === 0}
            <p class="empty">No dated papers in this scope.</p>
          {:else}
            <div class="chart" bind:this={chartContainer} data-testid="summary-chart"></div>
            {#if chartError}
              <p class="empty">Chart unavailable in this environment.</p>
            {/if}
          {/if}
        </div>
      </div>
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

  h3 {
    font-size: 0.9rem;
    margin: 0 0 0.4rem;
  }

  .muted,
  .hintline,
  .meta {
    color: #64717f;
    font-size: 0.85rem;
  }

  .meta {
    margin: 0 0 0.8rem;
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

  .grid {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr));
  }

  .block ol {
    margin: 0;
    padding-left: 1.2rem;
  }

  .block li {
    margin: 0.25rem 0;
  }

  .link {
    background: none;
    border: none;
    color: #1d4ed8;
    cursor: pointer;
    font: inherit;
    min-height: 0;
    padding: 0;
    text-align: left;
    text-decoration: underline;
  }

  .missing-title {
    color: #21303d;
  }

  .badge {
    color: #64717f;
    font-size: 0.78rem;
    margin-left: 0.3rem;
  }

  .import {
    background: #0f766e;
    border-color: #0f766e;
    font-size: 0.75rem;
    margin-left: 0.4rem;
    min-height: 0;
    padding: 0.1rem 0.45rem;
  }

  .selcount {
    align-self: center;
    color: #64717f;
    font-size: 0.9rem;
  }

  .chart {
    height: 16rem;
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

  .empty {
    color: #64717f;
    font-size: 0.85rem;
  }
</style>
