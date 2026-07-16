<!-- ShelfPicker — dropdown for picking (and optionally creating) a shelf. Props: client, autofocus,
     value (bindable — selected shelf id, '' for none), label, disabled, modifiableOnly (hide shelves
     the caller can't modify), excludeDefault (hide the Inbox/default shelf). Events/callbacks: none
     — caller reads back bound `value`. Non-obvious lifecycle/state: loads the shared shelves store
     on mount; inline "create shelf" is gated on canManageStructure and publishes through
     refreshShelves so every other picker sees the new shelf too. -->
<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiClient } from '../api/client';
  import { ensureShelves, refreshShelves, shelves } from '../lib/catalog';
  import { focusOnMount } from '../lib/focus';
  import { canManageStructure } from '../lib/session';

  export let client: ApiClient;
  // Focus the shelf select on mount (when opened inside a popup) — batch10 #6.
  export let autofocus = false;
  // Two-way bound: the selected shelf id, or '' for "no shelf".
  export let value = '';
  export let label = 'Add to shelf';
  export let disabled = false;
  // When true, hide shelves the caller can't modify (uses the `can_modify` hint from listShelves).
  // Default false keeps every existing caller (BatchImport/ImportPage) showing all visible shelves.
  export let modifiableOnly = false;
  // When true, hide the default/Inbox shelf (the loose-paper fallback): moving a paper *into* the
  // Inbox makes no sense. Default false so import/other callers still see it.
  export let excludeDefault = false;

  let filter = '';
  let loaded = false;
  let error = '';

  // Inline "create shelf" (so you can make one and pick it without leaving the import/paper context,
  // which a page reload would clear). Gated on canManageStructure to match the backend's floor.
  let creating = false;
  let newName = '';
  let createBusy = false;
  let createError = '';

  onMount(async () => {
    try {
      await ensureShelves(client);
    } catch (e) {
      error = e instanceof Error ? e.message : 'Could not load shelves';
    } finally {
      loaded = true;
    }
  });

  async function createShelf(): Promise<void> {
    const name = newName.trim();
    if (!name || createBusy) return;
    createBusy = true;
    createError = '';
    try {
      const shelf = await client.createShelf({ name });
      // Publish to every subscribed dropdown, then select the new shelf here.
      await refreshShelves(client);
      value = shelf.id;
      newName = '';
      creating = false;
    } catch (e) {
      createError = e instanceof Error ? e.message : 'Could not create shelf';
    } finally {
      createBusy = false;
    }
  }

  // The server lists shelves the caller may SEE and (since Phase N) flags which are modifiable.
  // When `modifiableOnly`, drop the ones the caller can't modify so the picker can't offer a
  // doomed add; otherwise show all visible shelves and rely on the backend's 403 at commit time.
  $: visible = $shelves
    .filter((s) => !modifiableOnly || s.can_modify)
    .filter((s) => !excludeDefault || !s.is_default);
  $: filtered = filter.trim()
    ? visible.filter((s) => s.name.toLowerCase().includes(filter.trim().toLowerCase()))
    : visible;
</script>

<div class="shelf-picker">
  <label>
    <span>{label}</span>
    {#if $shelves.length > 6}
      <input
        type="search"
        bind:value={filter}
        placeholder="Filter shelves…"
        aria-label="Filter shelves"
        {disabled}
      />
    {/if}
    <select bind:value {disabled} aria-label={label} use:focusOnMount={autofocus}>
      <option value="">No shelf</option>
      {#each filtered as shelf (shelf.id)}
        <option value={shelf.id}>{shelf.name}</option>
      {/each}
    </select>
  </label>
  {#if loaded && $shelves.length === 0 && !error}
    <p class="hint">No shelves yet — create one below.</p>
  {/if}
  {#if error}<p class="hint warn">{error}</p>{/if}

  {#if $canManageStructure}
    {#if creating}
      <form class="inline-create" on:submit|preventDefault={createShelf}>
        <input
          type="text"
          bind:value={newName}
          placeholder="New shelf name"
          aria-label="New shelf name"
          disabled={createBusy || disabled}
        />
        <button type="submit" class="secondary" disabled={!newName.trim() || createBusy || disabled}>
          {createBusy ? 'Creating…' : 'Create'}
        </button>
        <button
          type="button"
          class="link"
          on:click={() => {
            creating = false;
            createError = '';
            newName = '';
          }}
          disabled={createBusy}>Cancel</button
        >
      </form>
      {#if createError}<p class="hint warn">{createError}</p>{/if}
    {:else}
      <button type="button" class="link add-shelf" on:click={() => (creating = true)} {disabled}>
        + New shelf
      </button>
    {/if}
  {/if}
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

  .inline-create {
    display: flex;
    gap: 0.4rem;
    align-items: center;
    margin-top: 0.4rem;
  }

  .inline-create input {
    flex: 1;
    min-width: 0;
  }

  .link {
    background: none;
    border: none;
    padding: 0;
    color: var(--accent, var(--ink-muted));
    cursor: pointer;
    font-size: 0.8rem;
    text-decoration: underline;
  }

  .link:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .add-shelf {
    margin-top: 0.4rem;
  }
</style>
