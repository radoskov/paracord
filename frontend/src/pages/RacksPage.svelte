<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';

  import { ApiClient, type Rack, type Shelf } from '../api/client';
  import ExportDialog from '../components/ExportDialog.svelte';
  import { selectedRackId } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let racks: Rack[] = [];
  let selected: Rack | null = null;
  let rackShelves: Shelf[] = [];
  let allShelves: Shelf[] = [];
  let newRackName = '';
  let pickShelfId = '';
  let loading = false;
  let message = '';

  onMount(load);

  $: availableShelves = allShelves.filter((s) => !rackShelves.some((rs) => rs.id === s.id));

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

  async function load(): Promise<void> {
    await run(async () => {
      [racks, allShelves] = await Promise.all([client.listRacks(), client.listShelves()]);
      if (selected) selected = racks.find((r) => r.id === selected?.id) ?? null;
    });
    if (!selected) {
      const remembered = get(selectedRackId);
      const rack = remembered ? racks.find((r) => r.id === remembered) : undefined;
      if (rack) await select(rack);
    }
  }

  async function select(rack: Rack): Promise<void> {
    selected = rack;
    selectedRackId.set(rack.id);
    pickShelfId = '';
    await run(async () => {
      rackShelves = await client.listRackShelves(rack.id);
    });
  }

  async function createRack(): Promise<void> {
    await run(async () => {
      const rack = await client.createRack({ name: newRackName });
      newRackName = '';
      racks = await client.listRacks();
      await select(rack);
    }, 'Rack created');
  }

  async function addShelf(): Promise<void> {
    if (!selected || !pickShelfId) return;
    const rack = selected;
    await run(async () => {
      await client.addShelfToRack(rack.id, pickShelfId);
      pickShelfId = '';
      rackShelves = await client.listRackShelves(rack.id);
    }, 'Shelf added to rack');
  }

  async function removeShelf(shelfId: string): Promise<void> {
    if (!selected) return;
    const rack = selected;
    await run(async () => {
      await client.removeShelfFromRack(rack.id, shelfId);
      rackShelves = await client.listRackShelves(rack.id);
    }, 'Shelf removed');
  }

  async function archive(): Promise<void> {
    if (!selected) return;
    if (!window.confirm(`Archive rack “${selected.name}”?`)) return;
    const rack = selected;
    await run(async () => {
      await client.updateRack(rack.id, { status: 'archived' });
      selected = null;
      selectedRackId.set(null);
      rackShelves = [];
      racks = await client.listRacks();
    }, 'Rack archived');
  }
</script>

<section class="layout">
  {#if message}<p class="muted msg">{message}</p>{/if}

  <div class="card list">
    <h2>Racks</h2>
    <form on:submit|preventDefault={createRack} class="row">
      <input bind:value={newRackName} placeholder="New rack name" aria-label="New rack name" />
      <button type="submit" disabled={!newRackName.trim() || loading} title="Create a new rack">
        Add
      </button>
    </form>
    {#if racks.length === 0}
      <p class="empty">No racks yet. A rack groups several shelves together.</p>
    {:else}
      <ul class="rack-list">
        {#each racks as rack (rack.id)}
          <li>
            <button
              type="button"
              class="secondary item"
              class:active={selected?.id === rack.id}
              on:click={() => select(rack)}
              title="Open this rack"
            >
              <strong>{rack.name}</strong>
              {#if rack.status !== 'active'}<span class="badge">{rack.status}</span>{/if}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  <div class="card detail">
    {#if !selected}
      <p class="empty">Select a rack on the left to manage the shelves it contains.</p>
    {:else}
      <div class="head">
        <div>
          <span class="muted">Rack</span>
          <h2>{selected.name}</h2>
        </div>
        <button type="button" class="secondary" on:click={archive} disabled={loading}
          title="Archive this rack (asks for confirmation)">Archive rack</button>
      </div>

      <div class="add-shelf">
        <h3>Add a shelf to this rack</h3>
        <div class="row">
          <select bind:value={pickShelfId} aria-label="Choose a shelf" title="Choose a shelf to add to this rack">
            <option value="">Choose a shelf…</option>
            {#each availableShelves as shelf (shelf.id)}
              <option value={shelf.id}>{shelf.name}</option>
            {/each}
          </select>
          <button type="button" on:click={addShelf} disabled={!pickShelfId || loading}
            title={pickShelfId ? 'Add the chosen shelf' : 'Choose a shelf first'}>Add shelf</button>
        </div>
        {#if availableShelves.length === 0}
          <p class="hintline">Every shelf is already in this rack (or none exist yet).</p>
        {:else if !pickShelfId}
          <p class="hintline">Pick a shelf above to enable “Add shelf”.</p>
        {/if}
      </div>

      <h3>Shelves in this rack ({rackShelves.length})</h3>
      {#if rackShelves.length === 0}
        <p class="empty">This rack is empty. Use “Add a shelf” above.</p>
      {:else}
        <ul class="member-list">
          {#each rackShelves as shelf (shelf.id)}
            <li>
              <span>{shelf.name}</span>
              <button type="button" class="secondary small" on:click={() => removeShelf(shelf.id)}
                disabled={loading} title="Remove this shelf from the rack">Remove</button>
            </li>
          {/each}
        </ul>
      {/if}

      <ExportDialog
        label={`rack "${selected.name}"`}
        disabled={loading}
        fetchExport={(format, style) =>
          client.exportCitations({ scope_type: 'rack', scope_id: selected!.id, format, style })}
      />
    {/if}
  </div>
</section>

<style>
  .layout {
    align-items: start;
    display: grid;
    gap: 1rem;
    grid-template-columns: minmax(14rem, 22rem) minmax(0, 1fr);
  }

  .msg {
    grid-column: 1 / -1;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0 0 0.6rem;
  }

  h3 {
    font-size: 0.95rem;
    margin: 1rem 0 0.5rem;
  }

  .row {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .rack-list,
  .member-list {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    list-style: none;
    margin: 0.6rem 0 0;
    padding: 0;
  }

  .item {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: flex-start;
    text-align: left;
    width: 100%;
  }

  .item.active {
    background: #dfece3;
    border-color: #8eb39a;
  }

  .head {
    align-items: center;
    display: flex;
    justify-content: space-between;
  }

  .head h2 {
    margin: 0;
  }

  .add-shelf {
    background: #f4f6f9;
    border: 1px solid #e1e7ee;
    border-radius: 6px;
    margin-top: 0.5rem;
    padding: 0.7rem;
  }

  .member-list li {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
  }

  .badge {
    background: #fde68a;
    border-radius: 0.25rem;
    color: #78350f;
    font-size: 0.7rem;
    padding: 0.05rem 0.35rem;
  }

  .small {
    min-height: 1.9rem;
    padding: 0.2rem 0.5rem;
  }

  @media (max-width: 820px) {
    .layout {
      grid-template-columns: 1fr;
    }
  }
</style>
