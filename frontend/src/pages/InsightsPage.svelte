<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type CitationGraphResponse,
    type GraphNodeMode,
    type Rack,
    type ScopeSummaryResponse,
    type SemanticSearchItem,
    type Shelf,
    type Topic,
  } from '../api/client';
  import CitationGraph from '../components/CitationGraph.svelte';
  import ExportDialog from '../components/ExportDialog.svelte';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let shelves: Shelf[] = [];
  let racks: Rack[] = [];
  let scopeType: 'library' | 'shelf' | 'rack' = 'library';
  let scopeId = '';
  let topics: Topic[] = [];
  let summary: ScopeSummaryResponse | null = null;
  let semanticQuery = '';
  let semanticResults: SemanticSearchItem[] = [];
  let semanticDegraded = false;
  let loading = false;
  let message = '';

  onMount(async () => {
    await run(async () => {
      [shelves, racks] = await Promise.all([client.listShelves(), client.listRacks()]);
    });
  });

  $: scope = {
    scopeType,
    scopeId: scopeType === 'library' ? null : scopeId || null,
  };

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

  async function loadGraph(nodeMode: GraphNodeMode): Promise<CitationGraphResponse> {
    return client.citationGraph({ scopeType: scope.scopeType, scopeId: scope.scopeId, nodeMode });
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
      const response = await client.semanticSearch(semanticQuery, 10);
      semanticResults = response.items;
      semanticDegraded = response.degraded === true;
    });
  }

  $: scopeReady = scopeType === 'library' || !!scopeId;
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
      {/if}
    </div>
    {#if !scopeReady}<p class="hintline">Pick a {scopeType} to run analyses on it.</p>{/if}
    {#if scopeReady}
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
    {/if}
  </div>

  <div class="card">
    <CitationGraph
      label={scopeType === 'library' ? '· whole library' : `· selected ${scopeType}`}
      disabled={loading || !scopeReady}
      load={loadGraph}
    />
  </div>

  <div class="grid">
    <div class="card">
      <div class="head">
        <h2>Topics</h2>
        <button type="button" on:click={modelTopics} disabled={loading || !scopeReady}
          title={scopeReady ? 'Cluster the scope’s papers into keyword topics' : `Pick a ${scopeType} first`}>Model topics</button>
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
        <button type="button" on:click={summarise} disabled={loading || !scopeReady}
          title={scopeReady ? 'Generate an extractive summary across the scope’s abstracts' : `Pick a ${scopeType} first`}>Summarise</button>
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
    <h2>Semantic search</h2>
    <p class="muted">Find papers by meaning across the whole library (lexical baseline embedder).</p>
    <form on:submit|preventDefault={semanticSearch} class="row">
      <input bind:value={semanticQuery} placeholder="e.g. attention mechanisms for translation" aria-label="Semantic query" />
      <button type="submit" disabled={!semanticQuery.trim() || loading}
        title={semanticQuery.trim() ? 'Search the library by meaning' : 'Type a query first'}>Search</button>
    </form>
    {#if semanticDegraded}
      <p class="degraded-hint" role="status">Semantic search is using the built-in baseline (sentence-transformers not configured).</p>
    {/if}
    {#if semanticResults.length > 0}
      <ul class="plain">
        {#each semanticResults as hit (hit.work_id)}
          <li><strong>{hit.title ?? 'Untitled'}</strong><small class="muted"> · {(hit.score * 100).toFixed(0)}%{hit.year ? ` · ${hit.year}` : ''}</small></li>
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
