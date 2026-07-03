<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type CitationGraphResponse,
    type GraphNodeMode,
    type GraphScopeType,
    type HybridSearchItem,
    type ImportBatch,
    type Rack,
    type SavedFilter,
    type ScopeSummaryResponse,
    type SearchMode,
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
  let semanticQuery = '';
  let semanticResults: HybridSearchItem[] = [];
  let semanticDegraded = false;
  // Search mode (HS5): hybrid (RRF of lexical + semantic, default), semantic-only, or lexical-only.
  let searchMode: SearchMode = 'hybrid';
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
    // Warm the lexical index so the first lexical/hybrid search is hot (best-effort).
    client.warmSearch?.().catch(() => undefined);
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

  async function semanticSearch(): Promise<void> {
    if (!semanticQuery.trim()) return;
    await run(async () => {
      const response = await client.search(semanticQuery, searchMode, 10);
      semanticResults = response.items;
      semanticDegraded = response.degraded === true;
    });
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

  <div class="card">
    <h2>Search</h2>
    <p class="muted">
      Find papers across the library. <strong>Hybrid</strong> fuses keyword (BM25F+) and meaning-based
      search; <strong>Semantic</strong> is meaning-only; <strong>Lexical</strong> is keyword-only.
    </p>
    <div class="modes" role="radiogroup" aria-label="Search mode">
      {#each [['hybrid', 'Hybrid'], ['semantic', 'Semantic'], ['lexical', 'Lexical']] as [value, label] (value)}
        <label class="mode" class:active={searchMode === value}>
          <input type="radio" name="search-mode" value={value} bind:group={searchMode} />
          {label}
        </label>
      {/each}
    </div>
    <form on:submit|preventDefault={semanticSearch} class="row">
      <input bind:value={semanticQuery} placeholder="e.g. attention mechanisms for translation" aria-label="Search query" />
      <button type="submit" disabled={!semanticQuery.trim() || loading}
        title={semanticQuery.trim() ? 'Search the library' : 'Type a query first'}>Search</button>
    </form>
    {#if semanticDegraded}
      <p class="degraded-hint" role="status">Semantic ranking is using the built-in baseline embedder (sentence-transformers / Ollama not configured).</p>
    {/if}
    {#if semanticResults.length > 0}
      <ul class="plain results">
        {#each semanticResults as hit (hit.work_id)}
          <li>
            <div class="result-head">
              <strong>{hit.title ?? 'Untitled'}</strong>
              <small class="muted"> · {(hit.score * 100).toFixed(0)}%{hit.year ? ` · ${hit.year}` : ''}</small>
              {#if searchMode === 'hybrid'}
                {#if hit.lexical_rank && hit.semantic_rank}
                  <span class="badge both" title="Matched by both keyword and semantic search">both</span>
                {:else if hit.lexical_rank}
                  <span class="badge lex" title="Matched by keyword search">keyword</span>
                {:else if hit.semantic_rank}
                  <span class="badge sem" title="Matched by semantic search">semantic</span>
                {/if}
              {/if}
            </div>
            {#if hit.passage}
              <p class="passage">{hit.section ? `${hit.section}: ` : ''}{hit.passage.length > 240 ? hit.passage.slice(0, 240) + '…' : hit.passage}</p>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
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

  .degraded-hint {
    margin: 0.5rem 0 0;
    padding: 0.4rem 0.6rem;
    border-radius: 0.375rem;
    background: #fef3c7;
    color: #78350f;
    font-size: 0.85rem;
  }

  .modes {
    display: inline-flex;
    gap: 0.25rem;
    margin: 0 0 0.5rem;
    border: 1px solid #d1d5db;
    border-radius: 0.5rem;
    padding: 0.15rem;
  }

  .mode {
    cursor: pointer;
    padding: 0.2rem 0.6rem;
    border-radius: 0.375rem;
    font-size: 0.85rem;
    user-select: none;
  }

  .mode.active {
    background: #2563eb;
    color: #fff;
  }

  .mode input {
    position: absolute;
    opacity: 0;
    width: 0;
    height: 0;
  }

  .results li {
    margin-bottom: 0.6rem;
  }

  .result-head {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    flex-wrap: wrap;
  }

  .badge {
    font-size: 0.7rem;
    padding: 0.05rem 0.4rem;
    border-radius: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }

  .badge.both {
    background: #dcfce7;
    color: #166534;
  }

  .badge.lex {
    background: #e0e7ff;
    color: #3730a3;
  }

  .badge.sem {
    background: #fae8ff;
    color: #86198f;
  }

  .passage {
    margin: 0.2rem 0 0;
    font-size: 0.85rem;
    color: #4b5563;
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
    color: #64717f;
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
