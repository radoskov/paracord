<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type EmbeddingModelInfo,
    type HybridSearchItem,
    type SearchMode,
    type Work,
  } from '../api/client';
  import Modal from '../components/Modal.svelte';
  import ShelfPicker from '../components/ShelfPicker.svelte';
  import WorkDetail from '../components/WorkDetail.svelte';
  import { pendingLibraryOpen } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let query = '';
  let mode: SearchMode = 'hybrid';
  let embeddingModel = ''; // '' = default; a model_name; or 'multimode'
  let results: HybridSearchItem[] = [];
  let loading = false;
  let message = '';
  let searched = false;
  let degraded = false;
  let degradedReason: string | null = null;

  // Embedding-model selector, populated from the admin endpoint. Reader-only sessions get a 403 —
  // we then hide the selector and fall back to the default model.
  let embeddingModels: EmbeddingModelInfo[] = [];
  let multimodeAvailable = false;
  let modelSelectorAvailable = false;

  // The result the user clicked, showing the action menu (jump / read / shelf / details).
  let activeResult: HybridSearchItem | null = null;
  // A paper opened in a WorkDetail modal (reader / metadata / citations).
  let detailWork: Work | null = null;
  // Add-to-shelf modal state.
  let shelfForResult: HybridSearchItem | null = null;
  let shelfId = '';

  onMount(async () => {
    try {
      const info = await client.listEmbeddingModels();
      embeddingModels = info.models;
      multimodeAvailable = info.multimode_available;
      modelSelectorAvailable = true;
    } catch {
      // Reader-only sessions can't see the admin list — fall back to the default model silently.
      modelSelectorAvailable = false;
    }
  });

  async function runSearch(): Promise<void> {
    if (!query.trim()) return;
    loading = true;
    message = '';
    activeResult = null;
    try {
      const response = await client.search(
        query,
        mode,
        20,
        embeddingModel || undefined,
      );
      results = response.items;
      degraded = response.degraded === true;
      degradedReason = response.degraded_reason ?? null;
      searched = true;
    } catch (error) {
      message = errorMessage(error);
    } finally {
      loading = false;
    }
  }

  function relevancePct(hit: HybridSearchItem): number {
    // Prefer the normalised 0..1 relevance; fall back to the raw score when absent.
    const value = hit.relevance ?? hit.score ?? 0;
    return Math.round(Math.max(0, Math.min(1, value)) * 100);
  }

  function toggleActions(hit: HybridSearchItem): void {
    activeResult = activeResult?.work_id === hit.work_id ? null : hit;
  }

  function jumpToLibrary(hit: HybridSearchItem): void {
    pendingLibraryOpen.set(hit.work_id);
    window.location.hash = '#library';
    activeResult = null;
  }

  async function openDetail(hit: HybridSearchItem): Promise<void> {
    activeResult = null;
    try {
      detailWork = await client.getWork(hit.work_id);
    } catch (error) {
      message = errorMessage(error);
    }
  }

  function openShelfPicker(hit: HybridSearchItem): void {
    activeResult = null;
    shelfForResult = hit;
    shelfId = '';
  }

  async function addToShelf(): Promise<void> {
    if (!shelfForResult || !shelfId) return;
    const hit = shelfForResult;
    try {
      await client.addWorkToShelf(shelfId, hit.work_id);
      message = `Added “${hit.title ?? 'paper'}” to the shelf`;
      shelfForResult = null;
    } catch (error) {
      message = errorMessage(error);
    }
  }
</script>

<section class="layout">
  <div class="card">
    <h2>Search</h2>
    <p class="muted">
      Find papers across the library. <strong>Hybrid</strong> fuses keyword (BM25F+) and meaning-based
      search; <strong>Semantic</strong> is meaning-only; <strong>Lexical</strong> is keyword-only.
    </p>

    <div class="modes" role="radiogroup" aria-label="Search mode">
      {#each [['hybrid', 'Hybrid'], ['semantic', 'Semantic'], ['lexical', 'Lexical']] as [value, label] (value)}
        <label class="mode" class:active={mode === value}>
          <input type="radio" name="search-mode" value={value} bind:group={mode} />
          {label}
        </label>
      {/each}
    </div>

    <form on:submit|preventDefault={runSearch} class="row">
      <input
        bind:value={query}
        placeholder="e.g. attention mechanisms for translation"
        aria-label="Search query"
      />
      {#if modelSelectorAvailable && mode !== 'lexical'}
        <select bind:value={embeddingModel} aria-label="Embedding model" title="Which embedding model to use for meaning-based ranking">
          <option value="">Default model</option>
          {#each embeddingModels as m (m.model_name)}
            <option value={m.model_name}>{m.model_name} ({m.provider})</option>
          {/each}
          {#if multimodeAvailable}
            <option value="multimode">Multimode (all)</option>
          {/if}
        </select>
      {/if}
      <button type="submit" disabled={!query.trim() || loading}
        title={query.trim() ? 'Search the library' : 'Type a query first'}>Search</button>
    </form>

    {#if message}<p class="muted msg">{message}</p>{/if}
    {#if degraded}
      <p class="degraded-hint" role="status">
        Semantic ranking degraded to the built-in baseline embedder{degradedReason ? ` — ${degradedReason}` : ''}.
      </p>
    {/if}
  </div>

  {#if searched}
    <div class="card">
      {#if results.length === 0}
        <p class="empty">No papers matched “{query}”.</p>
      {:else}
        <ul class="plain results">
          {#each results as hit (hit.work_id)}
            <li>
              <button type="button" class="result-row" on:click={() => toggleActions(hit)}
                title="Show actions for this paper">
                <div class="result-head">
                  <strong>{hit.title ?? 'Untitled'}</strong>
                  <span class="rel" title="Relevance">{relevancePct(hit)}%</span>
                  {#if hit.year}<small class="muted">{hit.year}</small>{/if}
                  {#if hit.lexical_rank && hit.semantic_rank}
                    <span class="badge both" title="Matched by both keyword and semantic search">both</span>
                  {:else if hit.lexical_rank}
                    <span class="badge lex" title="Matched by keyword search">keyword</span>
                  {:else if hit.semantic_rank}
                    <span class="badge sem" title="Matched by semantic search">semantic</span>
                  {/if}
                </div>
                {#if hit.passage}
                  <p class="passage">{hit.section ? `${hit.section}: ` : ''}{hit.passage.length > 240 ? hit.passage.slice(0, 240) + '…' : hit.passage}</p>
                {/if}
              </button>
              {#if activeResult?.work_id === hit.work_id}
                <div class="actions" role="group" aria-label="Result actions">
                  <button type="button" class="secondary" on:click={() => jumpToLibrary(hit)}
                    title="Select this paper in the Library tab">Open in Library</button>
                  <button type="button" class="secondary" on:click={() => openDetail(hit)}
                    title="Open the paper to read it and view metadata / citations">Read / details</button>
                  <button type="button" class="secondary" on:click={() => openShelfPicker(hit)}
                    title="Add this paper to a shelf">Add to shelf</button>
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}
</section>

{#if detailWork}
  <Modal title={detailWork.title ?? 'Paper'} wide onClose={() => (detailWork = null)}>
    {#key detailWork.id}
      <WorkDetail
        {client}
        work={detailWork}
        onUpdated={(w) => (detailWork = w)}
        onDeleted={() => (detailWork = null)}
        onClose={() => (detailWork = null)}
      />
    {/key}
  </Modal>
{/if}

{#if shelfForResult}
  <Modal title="Add to a shelf" onClose={() => (shelfForResult = null)}>
    <p class="muted">Add “{shelfForResult.title ?? 'this paper'}” to a shelf.</p>
    <ShelfPicker {client} bind:value={shelfId} modifiableOnly />
    <div class="row" style="margin-top:0.6rem">
      <button type="button" class="secondary" on:click={() => (shelfForResult = null)}>Cancel</button>
      <button type="button" on:click={addToShelf} disabled={!shelfId}
        title={shelfId ? 'Add to the chosen shelf' : 'Choose a shelf first'}>Add</button>
    </div>
  </Modal>
{/if}

<style>
  .layout {
    display: grid;
    gap: 1rem;
  }

  .msg {
    margin: 0.5rem 0 0;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0 0 0.5rem;
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

  .row {
    display: flex;
    gap: 0.5rem;
    align-items: flex-end;
  }

  .row input {
    flex: 1;
    min-width: 0;
  }

  .plain {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }

  .result-row {
    display: block;
    width: 100%;
    text-align: left;
    background: #fff;
    border: 1px solid var(--pg-border, #cbd5e1);
    border-radius: 6px;
    color: var(--pg-text, #1f2a36);
    padding: 0.6rem 0.7rem;
    cursor: pointer;
  }

  .result-row:hover {
    background: #f4f6f9;
  }

  .result-head {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    flex-wrap: wrap;
  }

  .rel {
    font-size: 0.78rem;
    font-weight: 700;
    color: #166534;
    background: #dcfce7;
    border-radius: 0.75rem;
    padding: 0.05rem 0.45rem;
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

  .actions {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    padding: 0.4rem 0.2rem 0.1rem;
  }
</style>
