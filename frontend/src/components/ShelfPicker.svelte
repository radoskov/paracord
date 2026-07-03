<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiClient, type Shelf } from '../api/client';

  export let client: ApiClient;
  // Two-way bound: the selected shelf id, or '' for "no shelf".
  export let value = '';
  export let label = 'Add to shelf';
  export let disabled = false;
  // When true, hide shelves the caller can't modify (uses the `can_modify` hint from listShelves).
  // Default false keeps every existing caller (BatchImport/ImportPage) showing all visible shelves.
  export let modifiableOnly = false;

  let shelves: Shelf[] = [];
  let filter = '';
  let loaded = false;
  let error = '';

  onMount(async () => {
    try {
      shelves = await client.listShelves();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Could not load shelves';
    } finally {
      loaded = true;
    }
  });

  // The server lists shelves the caller may SEE and (since Phase N) flags which are modifiable.
  // When `modifiableOnly`, drop the ones the caller can't modify so the picker can't offer a
  // doomed add; otherwise show all visible shelves and rely on the backend's 403 at commit time.
  $: visible = modifiableOnly ? shelves.filter((s) => s.can_modify) : shelves;
  $: filtered = filter.trim()
    ? visible.filter((s) => s.name.toLowerCase().includes(filter.trim().toLowerCase()))
    : visible;
</script>

<div class="shelf-picker">
  <label>
    <span>{label}</span>
    {#if shelves.length > 6}
      <input
        type="search"
        bind:value={filter}
        placeholder="Filter shelves…"
        aria-label="Filter shelves"
        {disabled}
      />
    {/if}
    <select bind:value {disabled} aria-label={label}>
      <option value="">No shelf</option>
      {#each filtered as shelf (shelf.id)}
        <option value={shelf.id}>{shelf.name}</option>
      {/each}
    </select>
  </label>
  {#if loaded && shelves.length === 0 && !error}
    <p class="hint">No shelves yet — create one under Shelves first.</p>
  {/if}
  {#if error}<p class="hint warn">{error}</p>{/if}
</div>

<style>
  .shelf-picker label {
    display: grid;
    gap: 0.3rem;
  }

  .shelf-picker span {
    font-size: 0.85rem;
    color: var(--ink-muted);
  }

  .shelf-picker select,
  .shelf-picker input {
    width: 100%;
  }

  .hint {
    margin: 0.3rem 0 0;
    font-size: 0.8rem;
    color: var(--ink-muted);
  }

  .warn {
    color: var(--status-warning);
  }
</style>
