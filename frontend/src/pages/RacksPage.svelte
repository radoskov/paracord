<!-- RacksPage — manage racks (groups of shelves): create/rename/archive/delete a rack, set its
     access level, and add/remove member shelves.
     Props: client (ApiClient).
     Events/callbacks: none (page-level; syncs the shared `racks`/`shelves` catalog stores).
     Non-obvious lifecycle/state: on mount, re-selects whichever rack was remembered in the
     cross-tab `selectedRackId` store, if it still exists; deleting a non-empty rack asks a second
     confirmation for whether to cascade-delete its shelves too (orphaned papers move to the
     default shelf) or just detach them. -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';

  import { ApiClient, type AccessLevel, type Rack, type Shelf } from '../api/client';
  import ExportDialog from '../components/ExportDialog.svelte';
  import { racks, refreshRacks, refreshShelves, shelves } from '../lib/catalog';
  import { selectedRackId } from '../lib/selection';
  import { canManageStructure, INSUFFICIENT_ROLE } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  // Access levels offered in the create form and per-row selects (mirrors the backend).
  const ACCESS_LEVELS: { value: AccessLevel; label: string }[] = [
    { value: 'open', label: 'Open — anyone may see; structure editors may modify' },
    { value: 'visible', label: 'Visible — anyone may see; needs a grant to modify' },
    { value: 'private', label: 'Private — only granted groups may see or modify' },
  ];

  let selected: Rack | null = null;
  let rackShelves: Shelf[] = [];
  let newRackName = '';
  let newRackAccess: AccessLevel = 'open';
  let renameName = '';
  let editDescription = '';
  let newRackDescription = '';
  let pickShelfId = '';
  let loading = false;
  let message = '';

  onMount(load);

  $: availableShelves = $shelves.filter((s) => !rackShelves.some((rs) => rs.id === s.id));

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
      await Promise.all([refreshRacks(client), refreshShelves(client)]);
      if (selected) selected = get(racks).find((r) => r.id === selected?.id) ?? null;
    });
    if (!selected) {
      const remembered = get(selectedRackId);
      const rack = remembered ? get(racks).find((r) => r.id === remembered) : undefined;
      if (rack) await select(rack);
    }
  }

  async function select(rack: Rack): Promise<void> {
    selected = rack;
    selectedRackId.set(rack.id);
    renameName = rack.name;
    editDescription = rack.description ?? '';
    pickShelfId = '';
    await run(async () => {
      rackShelves = await client.listRackShelves(rack.id);
    });
  }

  async function saveDescription(): Promise<void> {
    const rack = selected;
    if (!rack) return;
    await run(async () => {
      const updated = await client.updateRack(rack.id, {
        description: editDescription.trim() || null,
      });
      $racks = $racks.map((r) => (r.id === updated.id ? updated : r));
      if (selected?.id === updated.id) selected = updated;
    }, 'Description saved');
  }

  async function renameRack(): Promise<void> {
    if (!selected) return;
    const name = renameName.trim();
    if (!name || name === selected.name) return;
    const rack = selected;
    await run(async () => {
      const updated = await client.updateRack(rack.id, { name });
      $racks = $racks.map((r) => (r.id === updated.id ? updated : r));
      if (selected?.id === updated.id) selected = updated;
    }, 'Rack renamed');
  }

  async function createRack(): Promise<void> {
    await run(async () => {
      const rack = await client.createRack({
        name: newRackName,
        access_level: newRackAccess,
        ...(newRackDescription.trim() ? { description: newRackDescription.trim() } : {}),
      });
      newRackName = '';
      newRackDescription = '';
      newRackAccess = 'open';
      await refreshRacks(client);
      await select(rack);
    }, 'Rack created');
  }

  async function changeAccess(rack: Rack, accessLevel: AccessLevel): Promise<void> {
    if (accessLevel === rack.access_level) return;
    await run(async () => {
      const updated = await client.updateRack(rack.id, { access_level: accessLevel });
      $racks = $racks.map((r) => (r.id === updated.id ? updated : r));
      if (selected?.id === updated.id) selected = updated;
    }, 'Rack access level updated');
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
      await refreshRacks(client);
    }, 'Rack archived');
  }

  async function removeRack(): Promise<void> {
    if (!selected) return;
    const rack = selected;
    const n = rackShelves.length;
    if (
      !window.confirm(
        `Delete rack “${rack.name}”? This permanently removes the rack and can't be undone.`,
      )
    )
      return;
    // Two-step: only when the rack has shelves, ask whether to delete them too.
    let deleteShelves = false;
    if (n > 0) {
      deleteShelves = window.confirm(
        `This rack contains ${n} shelf${n === 1 ? '' : 'ves'}.\n\n` +
          `OK — also DELETE ${n === 1 ? 'that shelf' : 'those shelves'} (papers found only on ` +
          `${n === 1 ? 'it' : 'them'} move to the default shelf).\n` +
          `Cancel — KEEP the shelves; they just leave this rack.`,
      );
    }
    await run(async () => {
      await client.deleteRack(rack.id, deleteShelves);
      selected = null;
      selectedRackId.set(null);
      rackShelves = [];
      await refreshRacks(client);
      // Cascade deleted the contained shelves too — refresh the shared shelf store so pickers drop them.
      if (deleteShelves) await refreshShelves(client);
    }, deleteShelves ? 'Rack and its shelves deleted' : 'Rack deleted');
  }
</script>

<section class="layout">
  {#if message}<p class="muted msg">{message}</p>{/if}

  <div class="card list">
    <h2>Racks</h2>
    <form on:submit|preventDefault={createRack} class="create">
      <div class="row">
        <input bind:value={newRackName} placeholder="New rack name" aria-label="New rack name"
          disabled={!$canManageStructure} />
        <button type="submit" disabled={!newRackName.trim() || loading || !$canManageStructure}
          title={$canManageStructure ? 'Create a new rack' : INSUFFICIENT_ROLE}>
          Add
        </button>
      </div>
      <input bind:value={newRackDescription} placeholder="Description (optional)"
        aria-label="New rack description" disabled={!$canManageStructure} />
      <select bind:value={newRackAccess} aria-label="New rack access level" disabled={!$canManageStructure}
        title={$canManageStructure ? 'Who may see and modify the new rack' : INSUFFICIENT_ROLE}>
        {#each ACCESS_LEVELS as lvl}<option value={lvl.value}>{lvl.label}</option>{/each}
      </select>
      {#if !$canManageStructure}<p class="hintline">{INSUFFICIENT_ROLE} — only librarians and admins can create racks.</p>{/if}
    </form>
    {#if $racks.length === 0}
      <p class="empty">No racks yet. A rack groups several shelves together.</p>
    {:else}
      <ul class="rack-list">
        {#each $racks as rack (rack.id)}
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
          {#if selected.description}<p class="muted desc">{selected.description}</p>{/if}
        </div>
        <div class="head-actions">
          <label class="access-inline">
            <span class="muted">Access</span>
            <select
              value={selected.access_level}
              on:change={(e) => changeAccess(selected!, e.currentTarget.value as AccessLevel)}
              disabled={loading || !$canManageStructure}
              aria-label="Rack access level"
              title={$canManageStructure ? 'Who may see and modify this rack' : INSUFFICIENT_ROLE}
            >
              {#each ACCESS_LEVELS as lvl}<option value={lvl.value}>{lvl.label}</option>{/each}
            </select>
          </label>
          <button type="button" class="secondary" on:click={archive} disabled={loading || !$canManageStructure}
            title={$canManageStructure ? 'Archive this rack (asks for confirmation)' : INSUFFICIENT_ROLE}>Archive rack</button>
          <button type="button" class="danger" on:click={removeRack} disabled={loading || !$canManageStructure}
            title={$canManageStructure ? 'Delete this rack; optionally delete its shelves too' : INSUFFICIENT_ROLE}>Delete rack</button>
        </div>
      </div>

      <form on:submit|preventDefault={renameRack} class="rename">
        <input bind:value={renameName} aria-label="Rename rack" placeholder="Rack name"
          disabled={loading || !$canManageStructure} />
        <button type="submit" class="secondary"
          disabled={loading || !$canManageStructure || !renameName.trim() || renameName.trim() === selected.name}
          title={$canManageStructure ? 'Rename this rack' : INSUFFICIENT_ROLE}>Rename</button>
      </form>

      <form on:submit|preventDefault={saveDescription} class="rename">
        <input bind:value={editDescription} aria-label="Rack description"
          placeholder="Description (optional — what this rack collects)"
          disabled={loading || !$canManageStructure} />
        <button type="submit" class="secondary"
          disabled={loading || !$canManageStructure || editDescription.trim() === (selected.description ?? '')}
          title={$canManageStructure ? 'Save this rack’s description' : INSUFFICIENT_ROLE}>Save description</button>
      </form>

      <div class="add-shelf">
        <h3>Add a shelf to this rack</h3>
        <div class="row">
          <select bind:value={pickShelfId} aria-label="Choose a shelf" title="Choose a shelf to add to this rack"
            disabled={!$canManageStructure}>
            <option value="">Choose a shelf…</option>
            {#each availableShelves as shelf (shelf.id)}
              <option value={shelf.id}>{shelf.name}</option>
            {/each}
          </select>
          <button type="button" on:click={addShelf} disabled={!pickShelfId || loading || !$canManageStructure}
            title={!$canManageStructure ? INSUFFICIENT_ROLE : pickShelfId ? 'Add the chosen shelf' : 'Choose a shelf first'}>Add shelf</button>
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
                disabled={loading || !$canManageStructure}
                title={$canManageStructure ? 'Remove this shelf from the rack' : INSUFFICIENT_ROLE}>Remove</button>
            </li>
          {/each}
        </ul>
      {/if}

      <ExportDialog
        label={`rack "${selected.name}"`}
        disabled={loading}
        fetchExport={(format, style) =>
          client.exportCitations({ scope_type: 'rack', scope_id: selected!.id, format, style })}
        fetchStyles={() => client.listCitationStyles()}
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

  .row input,
  .row select {
    min-width: 0;
  }

  .list {
    min-width: 0;
  }

  .create {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(0, 1fr);
  }

  .create input,
  .create select {
    min-width: 0;
    width: 100%;
  }

  .head-actions {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .access-inline {
    align-items: center;
    display: flex;
    gap: 0.35rem;
    font-size: 0.85rem;
  }

  .hintline {
    color: var(--status-warning);
    font-size: 0.8rem;
    margin: 0;
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

  /* Accent-tinted selected state, kept on :hover too (higher specificity than the global
     button.secondary:hover) so the current rack stays obviously highlighted, not lost to hover. */
  .item.active,
  .item.active:hover {
    background: var(--surface-selected);
    border-color: var(--surface-selected-border);
    color: var(--surface-selected-text);
  }

  .head {
    align-items: center;
    display: flex;
    justify-content: space-between;
  }

  .head h2 {
    margin: 0;
  }

  .rename {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(0, 1fr) auto;
    margin-top: 0.6rem;
  }

  .rename input {
    min-width: 0;
  }

  .add-shelf {
    background: var(--surface-sunken);
    border: 1px solid var(--border-normal);
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
    background: var(--status-warning-bg);
    border-radius: 0.25rem;
    color: var(--status-warning);
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
