<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type CitationGraphResponse,
    type GraphNodeMode,
    type GraphScopeType,
    type ImportBatch,
    type Rack,
    type SavedFilter,
    type ScopeSummaryResponse,
    type Shelf,
    type Topic,
  } from '../api/client';
  import CitationGraph from '../components/CitationGraph.svelte';
  import ExportDialog from '../components/ExportDialog.svelte';
  import { pendingLibrarySearch, selectedPaperIds } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;
  // Whether the Insights tab is visible (#9). Forwarded to CitationGraph so it can resize +
  // relayout Cytoscape after the tab is shown again (it mis-sizes while hidden).
  export let visible = true;

  let shelves: Shelf[] = [];
  let racks: Rack[] = [];
  let scopeType: GraphScopeType = 'library';
  let scopeId = '';
  let topics: Topic[] = [];
  let summary: ScopeSummaryResponse | null = null;
  let loading = false;
  let message = '';
  // Phase B6 graph scopes.
  let graphSearchQuery = '';
  let batches: ImportBatch[] = [];
  let batchId = '';
  // Phase B7 saved-filter scope.
  let savedFilters: SavedFilter[] = [];
  let savedFilterId = '';
  // Live count of the library multi-selection (mirrored from LibraryPage).
  let selectedCount = 0;
  $: selectedCount = $selectedPaperIds.length;

  onMount(async () => {
    await run(async () => {
      [shelves, racks, batches, savedFilters] = await Promise.all([
        client.listShelves(),
        client.listRacks(),
        client.listImportBatches().catch(() => [] as ImportBatch[]),
        client.listSavedFilters().catch(() => [] as SavedFilter[]),
      ]);
    });
  });

  $: scope = {
    scopeType,
    scopeId: scopeType === 'library' ? null : scopeId || null,
  };

  // The classic scopes (library/shelf/rack) drive Topics, Summary and Export; the Phase B6 graph
  // scopes only apply to the citation graph.
  $: isClassicScope =
    scopeType === 'library' || scopeType === 'shelf' || scopeType === 'rack';

  async function run(fn: () => Promise<void>, ok?: string): Promise<void> {
    loading = true;
    message = '';
    try {
      await fn();
      if (ok) message = ok;
    } catch (error) {
      message = errorMessage(error);
    } finally {
      loading = false;
    }
  }

  async function loadGraph(
    nodeMode: GraphNodeMode,
    collapseVersions: boolean,
    colorBy: import('../api/client').GraphColorBy,
  ): Promise<CitationGraphResponse> {
    if (scopeType === 'search_result') {
      // Run the metadata search now and pass the resulting ids as the explicit work set.
      const works = (await client.listWorks({ q: graphSearchQuery, perPage: 500 })).items;
      return client.citationGraph({
        scopeType,
        workIds: works.map((w) => w.id),
        nodeMode,
        collapseVersions,
        colorBy,
      });
    }
    if (scopeType === 'selected_papers') {
      return client.citationGraph({
        scopeType,
        workIds: $selectedPaperIds,
        nodeMode,
        collapseVersions,
        colorBy,
      });
    }
    if (scopeType === 'import_batch') {
      return client.citationGraph({ scopeType, scopeId: batchId || null, nodeMode, collapseVersions, colorBy });
    }
    if (scopeType === 'saved_filter') {
      // The backend loads the caller's saved filter, resolves + visibility-clamps it to work ids.
      return client.citationGraph({ scopeType, scopeId: savedFilterId || null, nodeMode, collapseVersions, colorBy });
    }
    return client.citationGraph({
      scopeType: scope.scopeType,
      scopeId: scope.scopeId,
      nodeMode,
      collapseVersions,
      colorBy,
    });
  }

  // Topic (embedding-similarity) graph over the current scope (#6). Mirrors loadGraph's scope
  // resolution so both graph types share the same scope picker.
  async function loadTopicGraph(): Promise<import('../api/client').TopicGraphResponse> {
    if (scopeType === 'search_result') {
      const works = (await client.listWorks({ q: graphSearchQuery, perPage: 500 })).items;
      return client.topicGraph({ scopeType, workIds: works.map((w) => w.id) });
    }
    if (scopeType === 'selected_papers') {
      return client.topicGraph({ scopeType, workIds: $selectedPaperIds });
    }
    if (scopeType === 'import_batch') {
      return client.topicGraph({ scopeType, scopeId: batchId || null });
    }
    if (scopeType === 'saved_filter') {
      return client.topicGraph({ scopeType, scopeId: savedFilterId || null });
    }
    return client.topicGraph({ scopeType: scope.scopeType, scopeId: scope.scopeId });
  }

  // External graph node → jump to the Library search for its DOI so the user can import it (#8).
  function importExternal(doi: string): void {
    pendingLibrarySearch.set({ query: doi, mode: 'metadata' });
    if (typeof window !== 'undefined') window.location.hash = '#library';
  }

  async function modelTopics(): Promise<void> {
    await run(async () => {
      const response = await client.modelTopics({
        scopeType: scope.scopeType,
        scopeId: scope.scopeId,
        maxTopics: 6,
      });
      topics = response.topics;
      message = `Modelled ${response.topics.length} topics over ${response.work_count} papers`;
    });
  }

  async function summarise(): Promise<void> {
    await run(async () => {
      summary = await client.createScopeScope(scope.scopeType, scope.scopeId);
    }, 'Summary generated');
  }

  $: scopeReady =
    scopeType === 'library'
      ? true
      : scopeType === 'shelf' || scopeType === 'rack'
        ? !!scopeId
        : scopeType === 'search_result'
          ? !!graphSearchQuery.trim()
          : scopeType === 'selected_papers'
            ? selectedCount > 0
            : scopeType === 'import_batch'
              ? !!batchId
              : scopeType === 'saved_filter'
                ? !!savedFilterId
                : false;
</script>

<section class="layout">
  {#if message}<p class="muted msg">{message}</p>{/if}

  <div class="card scope">
    <h2>Scope</h2>
    <p class="muted">Choose what the graph, topics and summary below operate on.</p>
    <div class="row">
      <select bind:value={scopeType} aria-label="Scope type" title="What the analyses below operate on">
        <option value="library">Whole library</option>
        <option value="shelf">A shelf</option>
        <option value="rack">A rack</option>
        <option value="search_result">Search result</option>
        <option value="selected_papers">Selected papers</option>
        <option value="import_batch">From import batch</option>
        <option value="saved_filter">Saved filter</option>
      </select>
      {#if scopeType === 'shelf'}
        <select bind:value={scopeId} aria-label="Shelf" title="Choose the shelf to analyse">
          <option value="">Choose a shelf…</option>
          {#each shelves as shelf (shelf.id)}<option value={shelf.id}>{shelf.name}</option>{/each}
        </select>
      {:else if scopeType === 'rack'}
        <select bind:value={scopeId} aria-label="Rack" title="Choose the rack to analyse">
          <option value="">Choose a rack…</option>
          {#each racks as rack (rack.id)}<option value={rack.id}>{rack.name}</option>{/each}
        </select>
      {:else if scopeType === 'search_result'}
        <input
          bind:value={graphSearchQuery}
          aria-label="Graph search query"
          placeholder="Search papers to graph…"
          title="Papers matching this search are graphed"
        />
      {:else if scopeType === 'selected_papers'}
        <span class="selcount" aria-label="Selected papers count">{selectedCount} paper{selectedCount === 1 ? '' : 's'} selected</span>
      {:else if scopeType === 'import_batch'}
        <select bind:value={batchId} aria-label="Import batch" title="Choose the import batch to graph">
          <option value="">Choose an import batch…</option>
          {#each batches as batch (batch.id)}
            <option value={batch.id}>{batch.input_type} · {batch.work_count ?? 0} paper{(batch.work_count ?? 0) === 1 ? '' : 's'} · {new Date(batch.created_at).toLocaleDateString()}</option>
          {/each}
        </select>
      {:else if scopeType === 'saved_filter'}
        <select bind:value={savedFilterId} aria-label="Saved filter" title="Choose the saved filter to analyse">
          <option value="">Choose a saved filter…</option>
          {#each savedFilters as filter (filter.id)}<option value={filter.id}>{filter.name}</option>{/each}
        </select>
      {/if}
    </div>
    {#if !scopeReady}
      <p class="hintline">
        {#if scopeType === 'search_result'}Type a search to graph its results.
        {:else if scopeType === 'selected_papers'}Select papers in the Library tab to graph them.
        {:else if scopeType === 'import_batch'}Pick an import batch to graph its papers.
        {:else if scopeType === 'saved_filter'}Pick a saved filter to graph its papers.
        {:else}Pick a {scopeType} to run analyses on it.{/if}
      </p>
    {/if}
    {#if scopeReady && isClassicScope}
      <div style="margin-top:0.6rem">
        <ExportDialog
          label={`this ${scopeType}`}
          fetchExport={(format, style) =>
            client.exportCitations({
              scope_type: scopeType,
              scope_id: scopeId || undefined,
              format,
              style,
            })}
          fetchStyles={() => client.listCitationStyles()}
        />
      </div>
    {:else if scopeReady && scopeType === 'saved_filter'}
      <div style="margin-top:0.6rem">
        <ExportDialog
          label="this saved filter"
          fetchExport={(format, style) =>
            client.exportCitations({
              scope_type: 'saved_filter',
              scope_id: savedFilterId || undefined,
              format,
              style,
            })}
          fetchStyles={() => client.listCitationStyles()}
        />
      </div>
    {/if}
  </div>

  <div class="card">
    <CitationGraph
      label={scopeType === 'library' ? '· whole library' : `· ${scopeType.replace('_', ' ')}`}
      disabled={loading || !scopeReady}
      load={loadGraph}
      loadTopic={loadTopicGraph}
      onImportExternal={importExternal}
      {visible}
    />
  </div>

  <div class="grid">
    <div class="card">
      <div class="head">
        <h2>Topics</h2>
        <button type="button" on:click={modelTopics} disabled={loading || !scopeReady || !isClassicScope}
          title={isClassicScope ? (scopeReady ? 'Cluster the scope’s papers into keyword topics' : `Pick a ${scopeType} first`) : 'Topics work on a library, shelf or rack scope'}>Model topics</button>
      </div>
      {#if topics.length === 0}
        <p class="empty">No topics yet — click “Model topics”.</p>
      {:else}
        <ul class="plain">
          {#each topics as topic (topic.topic_id)}
            <li><strong>{topic.keywords.join(', ')}</strong><small class="muted"> · {topic.work_count} papers</small></li>
          {/each}
        </ul>
      {/if}
    </div>

    <div class="card">
      <div class="head">
        <h2>Scope summary</h2>
        <button type="button" on:click={summarise} disabled={loading || !scopeReady || !isClassicScope}
          title={isClassicScope ? (scopeReady ? 'Generate an extractive summary across the scope’s abstracts' : `Pick a ${scopeType} first`) : 'Summaries work on a library, shelf or rack scope'}>Summarise</button>
      </div>
      {#if !summary}
        <p class="empty">No summary yet — click “Summarise”.</p>
      {:else}
        <p class="summary-text">{summary.text}</p>
        <p class="hintline">{summary.summary_type} · {summary.work_count} papers · {summary.model_name ?? 'local'}</p>
      {/if}
    </div>
  </div>
</section>

<style>
  .layout {
    display: grid;
    gap: 1rem;
  }

  .msg {
    margin: 0;
  }

  .grid {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  }

  h2 {
    font-size: 1.05rem;
    margin: 0 0 0.5rem;
  }

  .head {
    align-items: center;
    display: flex;
    justify-content: space-between;
  }

  .head h2 {
    margin: 0;
  }

  .row {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .scope .row {
    grid-template-columns: minmax(0, 12rem) minmax(0, 1fr);
  }

  .selcount {
    align-self: center;
    color: var(--ink-muted);
    font-size: 0.9rem;
  }

  .plain {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
  }

  .summary-text {
    line-height: 1.5;
    margin: 0.3rem 0;
  }
</style>
