<script lang="ts">
  // Shared scope picker for the analysis tabs (Insights audit C3, 2026-07-14). One select for the
  // seven scope types + the matching sub-control (shelf/rack/batch/saved-filter dropdowns, search
  // input, selection count). Owns loading its own dropdown data; readiness is reported through the
  // bindable `ready` prop. Pages keep the per-type state (bound) so they can build requests via
  // lib/scope.resolveScopeRequest.
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type GraphScopeType,
    type ImportBatch,
    type SavedFilter,
  } from '../api/client';
  import { ensureRacks, ensureShelves, racks, shelves } from '../lib/catalog';
  import { scopeSelectionReady } from '../lib/scope';
  import { selectedPaperIds } from '../lib/selection';

  export let client: ApiClient;
  export let scopeType: GraphScopeType = 'library';
  /** Shelf or rack id. */
  export let scopeId = '';
  export let searchQuery = '';
  export let batchId = '';
  export let savedFilterId = '';
  /** Bindable out: whether the selection identifies a concrete set of papers. */
  export let ready = true;
  /** The tab's verb for hint text: "graph", "visualize", "summarize"… */
  export let verb = 'analyze';
  /** data-testid prefix so each tab keeps distinct, stable test hooks. */
  export let testid = 'scope';

  let batches: ImportBatch[] = [];
  let savedFilters: SavedFilter[] = [];

  onMount(async () => {
    // Prime the shared catalog stores so newly created shelves/racks appear live; batch/filter
    // lists are per-picker. Failures degrade to empty dropdowns rather than breaking the tab.
    [, , batches, savedFilters] = await Promise.all([
      ensureShelves(client).catch(() => []),
      ensureRacks(client).catch(() => []),
      client.listImportBatches().catch(() => [] as ImportBatch[]),
      client.listSavedFilters().catch(() => [] as SavedFilter[]),
    ]);
  });

  $: selectedCount = $selectedPaperIds.length;
  $: ready = scopeSelectionReady(
    { scopeType, scopeId, searchQuery, batchId, savedFilterId },
    selectedCount,
  );

  function batchLabel(batch: ImportBatch): string {
    const count = batch.work_count ?? 0;
    const papers = `${count} paper${count === 1 ? '' : 's'}`;
    return `${batch.input_type} · ${papers} · ${new Date(batch.created_at).toLocaleDateString()}`;
  }
</script>

<div class="picker">
  <label>
    Scope
    <select
      bind:value={scopeType}
      aria-label="Scope type"
      data-testid={`${testid}-scope-type`}
      title="Which papers to include — the whole library, a shelf, a rack, a search result, the papers selected in the Library tab, an import batch, or a saved filter"
    >
      <option value="library">Whole library</option>
      <option value="shelf">A shelf</option>
      <option value="rack">A rack</option>
      <option value="search_result">Search result</option>
      <option value="selected_papers">Selected papers</option>
      <option value="import_batch">Import batch</option>
      <option value="saved_filter">Saved filter</option>
    </select>
  </label>

  {#if scopeType === 'shelf'}
    <label>Shelf
      <select bind:value={scopeId} aria-label="Shelf" data-testid={`${testid}-scope-id`}
        title={`Choose the shelf to ${verb}`}>
        <option value="">Choose a shelf…</option>
        {#each $shelves as shelf (shelf.id)}<option value={shelf.id}>{shelf.name}</option>{/each}
      </select>
    </label>
  {:else if scopeType === 'rack'}
    <label>Rack
      <select bind:value={scopeId} aria-label="Rack" data-testid={`${testid}-scope-id`}
        title={`Choose the rack to ${verb}`}>
        <option value="">Choose a rack…</option>
        {#each $racks as rack (rack.id)}<option value={rack.id}>{rack.name}</option>{/each}
      </select>
    </label>
  {:else if scopeType === 'search_result'}
    <label>Query
      <input
        bind:value={searchQuery}
        aria-label="Scope search query"
        data-testid={`${testid}-scope-search`}
        placeholder="Search papers…"
        title={`Papers matching this search are ${verb}d`}
      />
    </label>
  {:else if scopeType === 'selected_papers'}
    <span class="selcount" aria-label="Selected papers count"
      >{selectedCount} paper{selectedCount === 1 ? '' : 's'} selected</span>
  {:else if scopeType === 'import_batch'}
    <label>Import batch
      <select bind:value={batchId} aria-label="Import batch" data-testid={`${testid}-scope-id`}
        title={`Choose the import batch to ${verb}`}>
        <option value="">Choose an import batch…</option>
        {#each batches as batch (batch.id)}<option value={batch.id}>{batchLabel(batch)}</option>{/each}
      </select>
    </label>
  {:else if scopeType === 'saved_filter'}
    <label>Saved filter
      <select bind:value={savedFilterId} aria-label="Saved filter" data-testid={`${testid}-scope-id`}
        title={`Choose the saved filter to ${verb}`}>
        <option value="">Choose a saved filter…</option>
        {#each savedFilters as filter (filter.id)}<option value={filter.id}>{filter.name}</option>{/each}
      </select>
    </label>
  {/if}

  <slot />
</div>

{#if !ready}
  <p class="hintline">
    {#if scopeType === 'search_result'}Type a search to {verb} its results.
    {:else if scopeType === 'selected_papers'}Select papers in the Library tab first.
    {:else if scopeType === 'import_batch'}Pick an import batch to {verb} its papers.
    {:else if scopeType === 'saved_filter'}Pick a saved filter to {verb} its papers.
    {:else}Pick a {scopeType} to {verb}.{/if}
  </p>
{/if}

<style>
  .picker {
    align-items: flex-end;
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
  }

  label {
    color: var(--ink-strong);
    display: flex;
    flex-direction: column;
    font-size: 0.8rem;
    font-weight: 700;
    gap: 0.2rem;
  }

  select,
  input {
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    font: inherit;
    font-weight: 400;
    max-width: 18rem;
    padding: 0.3rem 0.5rem;
  }

  .selcount {
    align-self: center;
    color: var(--ink-muted);
    font-size: 0.9rem;
  }

  .hintline {
    color: var(--ink-muted);
    font-size: 0.85rem;
    margin: 0.4rem 0 0;
    /* Always take a full row, even when the picker sits inside a flex "controls" strip. */
    flex-basis: 100%;
    width: 100%;
  }
</style>
