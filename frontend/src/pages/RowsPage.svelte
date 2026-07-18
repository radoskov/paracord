<!-- RowsPage — manage rows (the broadest grouping layer; a row groups several racks): create/rename/
     archive/delete a row, set its access level, and add/remove member racks. Mirrors RacksPage one
     hop up (Row ⊃ Rack ⊃ Shelf ⊃ Paper).
     Props: client (ApiClient).
     Non-obvious: on mount, re-selects whichever row was remembered in the cross-tab `selectedRowId`
     store; deleting a non-empty row asks a second confirmation for whether to cascade-delete its
     racks too (their shelves survive) or just detach them. -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';

  import { ApiClient, type AccessLevel, type Rack, type Row } from '../api/client';
  import ExportDialog from '../components/ExportDialog.svelte';
  import { racks, refreshRacks, refreshRows, rows } from '../lib/catalog';
  import { selectedRowId } from '../lib/selection';
  import { canManageStructure, INSUFFICIENT_ROLE } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  const ACCESS_LEVELS: { value: AccessLevel; label: string }[] = [
    { value: 'open', label: 'Open — anyone may see; structure editors may modify' },
    { value: 'visible', label: 'Visible — anyone may see; needs a grant to modify' },
    { value: 'private', label: 'Private — only granted groups may see or modify' },
  ];

  let selected: Row | null = null;
  let rowRacks: Rack[] = [];
  let newRowName = '';
  let newRowAccess: AccessLevel = 'open';
  let renameName = '';
  let editDescription = '';
  let newRowDescription = '';
  let pickRackId = '';
  let loading = false;
  let message = '';

  onMount(load);

  $: availableRacks = $racks.filter((r) => !rowRacks.some((rr) => rr.id === r.id));

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
      await Promise.all([refreshRows(client), refreshRacks(client)]);
      if (selected) selected = get(rows).find((r) => r.id === selected?.id) ?? null;
    });
    if (!selected) {
      const remembered = get(selectedRowId);
      const row = remembered ? get(rows).find((r) => r.id === remembered) : undefined;
      if (row) await select(row);
    }
  }

  async function select(row: Row): Promise<void> {
    selected = row;
    selectedRowId.set(row.id);
    renameName = row.name;
    editDescription = row.description ?? '';
    pickRackId = '';
    await run(async () => {
      rowRacks = await client.listRowRacks(row.id);
    });
  }

  async function saveDescription(): Promise<void> {
    const row = selected;
    if (!row) return;
    await run(async () => {
      const updated = await client.updateRow(row.id, {
        description: editDescription.trim() || null,
      });
      $rows = $rows.map((r) => (r.id === updated.id ? updated : r));
      if (selected?.id === updated.id) selected = updated;
    }, 'Description saved');
  }

  async function renameRow(): Promise<void> {
    if (!selected) return;
    const name = renameName.trim();
    if (!name || name === selected.name) return;
    const row = selected;
    await run(async () => {
      const updated = await client.updateRow(row.id, { name });
      $rows = $rows.map((r) => (r.id === updated.id ? updated : r));
      if (selected?.id === updated.id) selected = updated;
    }, 'Row renamed');
  }

  async function createRow(): Promise<void> {
    await run(async () => {
      const row = await client.createRow({
        name: newRowName,
        access_level: newRowAccess,
        ...(newRowDescription.trim() ? { description: newRowDescription.trim() } : {}),
      });
      newRowName = '';
      newRowDescription = '';
      newRowAccess = 'open';
      await refreshRows(client);
      await select(row);
    }, 'Row created');
  }

  async function changeAccess(row: Row, accessLevel: AccessLevel): Promise<void> {
    if (accessLevel === row.access_level) return;
    await run(async () => {
      const updated = await client.updateRow(row.id, { access_level: accessLevel });
      $rows = $rows.map((r) => (r.id === updated.id ? updated : r));
      if (selected?.id === updated.id) selected = updated;
    }, 'Row access level updated');
  }

  async function addRack(): Promise<void> {
    if (!selected || !pickRackId) return;
    const row = selected;
    await run(async () => {
      await client.addRackToRow(row.id, pickRackId);
      pickRackId = '';
      rowRacks = await client.listRowRacks(row.id);
    }, 'Rack added to row');
  }

  async function removeRack(rackId: string): Promise<void> {
    if (!selected) return;
    const row = selected;
    await run(async () => {
      await client.removeRackFromRow(row.id, rackId);
      rowRacks = await client.listRowRacks(row.id);
    }, 'Rack removed');
  }

  async function archive(): Promise<void> {
    if (!selected) return;
    if (!window.confirm(`Archive row “${selected.name}”?`)) return;
    const row = selected;
    await run(async () => {
      await client.updateRow(row.id, { status: 'archived' });
      selected = null;
      selectedRowId.set(null);
      rowRacks = [];
      await refreshRows(client);
    }, 'Row archived');
  }

  async function removeRow(): Promise<void> {
    if (!selected) return;
    const row = selected;
    const n = rowRacks.length;
    if (
      !window.confirm(
        `Delete row “${row.name}”? This permanently removes the row and can't be undone.`,
      )
    )
      return;
    // Two-step: only when the row has racks, ask whether to delete them too.
    let deleteRacks = false;
    if (n > 0) {
      deleteRacks = window.confirm(
        `This row contains ${n} rack${n === 1 ? '' : 's'}.\n\n` +
          `OK — also DELETE ${n === 1 ? 'that rack' : 'those racks'} (their shelves survive).\n` +
          `Cancel — KEEP the racks; they just leave this row.`,
      );
    }
    await run(async () => {
      await client.deleteRow(row.id, deleteRacks);
      selected = null;
      selectedRowId.set(null);
      rowRacks = [];
      await refreshRows(client);
      // Cascade deleted the contained racks too — refresh the shared rack store so pickers drop them.
      if (deleteRacks) await refreshRacks(client);
    }, deleteRacks ? 'Row and its racks deleted' : 'Row deleted');
  }
</script>

<section class="layout">
  {#if message}<p class="muted msg">{message}</p>{/if}

  <div class="card list">
    <h2>Rows</h2>
    <form on:submit|preventDefault={createRow} class="create">
      <div class="row">
        <input bind:value={newRowName} placeholder="New row name" aria-label="New row name"
          disabled={!$canManageStructure} />
        <button type="submit" disabled={!newRowName.trim() || loading || !$canManageStructure}
          title={$canManageStructure ? 'Create a new row' : INSUFFICIENT_ROLE}>
          Add
        </button>
      </div>
      <input bind:value={newRowDescription} placeholder="Description (optional)"
        aria-label="New row description" disabled={!$canManageStructure} />
      <select bind:value={newRowAccess} aria-label="New row access level" disabled={!$canManageStructure}
        title={$canManageStructure ? 'Who may see and modify the new row' : INSUFFICIENT_ROLE}>
        {#each ACCESS_LEVELS as lvl}<option value={lvl.value}>{lvl.label}</option>{/each}
      </select>
      {#if !$canManageStructure}<p class="hintline">{INSUFFICIENT_ROLE} — only librarians and admins can create rows.</p>{/if}
    </form>
    {#if $rows.length === 0}
      <p class="empty">No rows yet. A row groups several racks together.</p>
    {:else}
      <ul class="row-list">
        {#each $rows as row (row.id)}
          <li>
            <button
              type="button"
              class="secondary item"
              class:active={selected?.id === row.id}
              on:click={() => select(row)}
              title="Open this row"
            >
              <strong>{row.name}</strong>
              {#if row.status !== 'active'}<span class="badge">{row.status}</span>{/if}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  <div class="card detail">
    {#if !selected}
      <p class="empty">Select a row on the left to manage the racks it contains.</p>
    {:else}
      <div class="head">
        <div>
          <span class="muted">Row</span>
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
              aria-label="Row access level"
              title={$canManageStructure ? 'Who may see and modify this row' : INSUFFICIENT_ROLE}
            >
              {#each ACCESS_LEVELS as lvl}<option value={lvl.value}>{lvl.label}</option>{/each}
            </select>
          </label>
          <button type="button" class="secondary" on:click={archive} disabled={loading || !$canManageStructure}
            title={$canManageStructure ? 'Archive this row (asks for confirmation)' : INSUFFICIENT_ROLE}>Archive row</button>
          <button type="button" class="danger" on:click={removeRow} disabled={loading || !$canManageStructure}
            title={$canManageStructure ? 'Delete this row; optionally delete its racks too' : INSUFFICIENT_ROLE}>Delete row</button>
        </div>
      </div>

      <form on:submit|preventDefault={renameRow} class="rename">
        <input bind:value={renameName} aria-label="Rename row" placeholder="Row name"
          disabled={loading || !$canManageStructure} />
        <button type="submit" class="secondary"
          disabled={loading || !$canManageStructure || !renameName.trim() || renameName.trim() === selected.name}
          title={$canManageStructure ? 'Rename this row' : INSUFFICIENT_ROLE}>Rename</button>
      </form>

      <form on:submit|preventDefault={saveDescription} class="rename">
        <input bind:value={editDescription} aria-label="Row description"
          placeholder="Description (optional — what this row collects)"
          disabled={loading || !$canManageStructure} />
        <button type="submit" class="secondary"
          disabled={loading || !$canManageStructure || editDescription.trim() === (selected.description ?? '')}
          title={$canManageStructure ? 'Save this row’s description' : INSUFFICIENT_ROLE}>Save description</button>
      </form>

      <div class="add-shelf">
        <h3>Add a rack to this row</h3>
        <div class="row">
          <select bind:value={pickRackId} aria-label="Choose a rack" title="Choose a rack to add to this row"
            disabled={!$canManageStructure}>
            <option value="">Choose a rack…</option>
            {#each availableRacks as rack (rack.id)}
              <option value={rack.id}>{rack.name}</option>
            {/each}
          </select>
          <button type="button" on:click={addRack} disabled={!pickRackId || loading || !$canManageStructure}
            title={!$canManageStructure ? INSUFFICIENT_ROLE : pickRackId ? 'Add the chosen rack' : 'Choose a rack first'}>Add rack</button>
        </div>
        {#if availableRacks.length === 0}
          <p class="hintline">Every rack is already in this row (or none exist yet).</p>
        {:else if !pickRackId}
          <p class="hintline">Pick a rack above to enable “Add rack”.</p>
        {/if}
      </div>

      <h3>Racks in this row ({rowRacks.length})</h3>
      {#if rowRacks.length === 0}
        <p class="empty">This row is empty. Use “Add a rack” above.</p>
      {:else}
        <ul class="member-list">
          {#each rowRacks as rack (rack.id)}
            <li>
              <span>{rack.name}</span>
              <button type="button" class="secondary small" on:click={() => removeRack(rack.id)}
                disabled={loading || !$canManageStructure}
                title={$canManageStructure ? 'Remove this rack from the row' : INSUFFICIENT_ROLE}>Remove</button>
            </li>
          {/each}
        </ul>
      {/if}

      <ExportDialog
        label={`row "${selected.name}"`}
        disabled={loading}
        fetchExport={(format, style) =>
          client.exportCitations({ scope_type: 'row', scope_id: selected!.id, format, style })}
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

  .row-list,
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
