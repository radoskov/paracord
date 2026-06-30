<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiClient, type Shelf } from '../api/client';

  export let client: ApiClient;
  // Two-way bound: the selected shelf id, or '' for "no shelf".
  export let value = '';
  export let label = 'Add to shelf';
  export let disabled = false;

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

  // The server only lists shelves the caller may SEE; it can't yet say which are *modifiable*
  // client-side, so we show all of them and rely on the backend's 403 at commit time if the user
  // lacks modify access. (Follow-up: surface a `modifiable` hint on ShelfRead to pre-filter.)
  $: filtered = filter.trim()
    ? shelves.filter((s) => s.name.toLowerCase().includes(filter.trim().toLowerCase()))
    : shelves;
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
    color: var(--muted, #555);
  }

  .shelf-picker select,
  .shelf-picker input {
    width: 100%;
  }

  .hint {
    margin: 0.3rem 0 0;
    font-size: 0.8rem;
    color: var(--muted, #777);
  }

  .warn {
    color: #b45309;
  }
</style>
