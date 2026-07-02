<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';

  import { ApiClient, type AccessLevel, type Shelf, type Work } from '../api/client';
  import ExportDialog from '../components/ExportDialog.svelte';
  import { selectedShelfId } from '../lib/selection';
  import { canManageStructure, INSUFFICIENT_ROLE } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  // Access levels offered in the create form and per-shelf select (mirrors the backend).
  const ACCESS_LEVELS: { value: AccessLevel; label: string }[] = [
    { value: 'open', label: 'Open — anyone may see; structure editors may modify' },
    { value: 'visible', label: 'Visible — anyone may see; needs a grant to modify' },
    { value: 'private', label: 'Private — only granted groups may see or modify' },
  ];

  let shelves: Shelf[] = [];
  let selected: Shelf | null = null;
  let shelfWorks: Work[] = [];
  let allWorks: Work[] = [];
  let newShelfName = '';
  let newShelfAccess: AccessLevel = 'open';
  let pickWorkId = '';
  let workFilter = '';
  let loading = false;
  let message = '';

  onMount(load);

  $: filteredWorks = allWorks.filter((work) => {
    const inShelf = shelfWorks.some((w) => w.id === work.id);
    const title = (work.canonical_title ?? '').toLowerCase();
    return !inShelf && title.includes(workFilter.toLowerCase());
  });

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
      [shelves, allWorks] = await Promise.all([client.listShelves(), client.listWorks()]);
      if (selected) selected = shelves.find((s) => s.id === selected?.id) ?? null;
    });
    // Restore the shelf left open on a previous visit to this tab.
    if (!selected) {
      const remembered = get(selectedShelfId);
      const shelf = remembered ? shelves.find((s) => s.id === remembered) : undefined;
      if (shelf) await select(shelf);
    }
  }

  async function select(shelf: Shelf): Promise<void> {
    selected = shelf;
    selectedShelfId.set(shelf.id);
    pickWorkId = '';
    await run(async () => {
      shelfWorks = await client.listShelfWorks(shelf.id);
    });
  }

  async function createShelf(): Promise<void> {
    await run(async () => {
      const shelf = await client.createShelf({ name: newShelfName, access_level: newShelfAccess });
      newShelfName = '';
      newShelfAccess = 'open';
      shelves = await client.listShelves();
      await select(shelf);
    }, 'Shelf created');
  }

  async function changeAccess(shelf: Shelf, accessLevel: AccessLevel): Promise<void> {
    if (accessLevel === shelf.access_level) return;
    await run(async () => {
      const updated = await client.updateShelf(shelf.id, { access_level: accessLevel });
      shelves = shelves.map((s) => (s.id === updated.id ? updated : s));
      if (selected?.id === updated.id) selected = updated;
    }, 'Shelf access level updated');
  }

  async function addWork(): Promise<void> {
    if (!selected || !pickWorkId) return;
    const shelf = selected;
    await run(async () => {
      await client.addWorkToShelf(shelf.id, pickWorkId);
      pickWorkId = '';
      shelfWorks = await client.listShelfWorks(shelf.id);
    }, 'Paper added to shelf');
  }

  async function removeWork(workId: string): Promise<void> {
    if (!selected) return;
    const shelf = selected;
    await run(async () => {
      await client.removeWorkFromShelf(shelf.id, workId);
      shelfWorks = await client.listShelfWorks(shelf.id);
    }, 'Paper removed');
  }

  async function archive(): Promise<void> {
    if (!selected) return;
    if (!window.confirm(`Archive shelf “${selected.name}”? It will be hidden from active lists.`))
      return;
    const shelf = selected;
    await run(async () => {
      await client.updateShelf(shelf.id, { status: 'archived' });
      selected = null;
      selectedShelfId.set(null);
      shelfWorks = [];
      shelves = await client.listShelves();
    }, 'Shelf archived');
  }

  async function removeShelf(): Promise<void> {
    if (!selected) return;
    if (
      !window.confirm(
        `Delete shelf “${selected.name}”? This can't be undone. Papers only on this shelf are ` +
          `moved to the default shelf; papers also on other shelves just leave this one.`,
      )
    )
      return;
    const shelf = selected;
    await run(async () => {
      await client.deleteShelf(shelf.id);
      selected = null;
      selectedShelfId.set(null);
      shelfWorks = [];
      shelves = await client.listShelves();
    }, 'Shelf deleted');
  }
</script>

<section class="layout">
  {#if message}<p class="muted msg">{message}</p>{/if}

  <div class="card list">
    <h2>Shelves</h2>
    <form on:submit|preventDefault={createShelf} class="create">
      <div class="row">
        <input bind:value={newShelfName} placeholder="New shelf name" aria-label="New shelf name"
          disabled={!$canManageStructure} />
        <button type="submit" disabled={!newShelfName.trim() || loading || !$canManageStructure}
          title={$canManageStructure ? 'Create a new shelf' : INSUFFICIENT_ROLE}>
          Add
        </button>
      </div>
      <select bind:value={newShelfAccess} aria-label="New shelf access level" disabled={!$canManageStructure}
        title={$canManageStructure ? 'Who may see and modify the new shelf' : INSUFFICIENT_ROLE}>
        {#each ACCESS_LEVELS as lvl}<option value={lvl.value}>{lvl.label}</option>{/each}
      </select>
      {#if !$canManageStructure}<p class="hintline">{INSUFFICIENT_ROLE} — only librarians and admins can create shelves.</p>{/if}
    </form>
    {#if shelves.length === 0}
      <p class="empty">No shelves yet. Create one to group related papers.</p>
    {:else}
      <ul class="shelf-list">
        {#each shelves as shelf (shelf.id)}
          <li>
            <button
              type="button"
              class="secondary item"
              class:active={selected?.id === shelf.id}
              on:click={() => select(shelf)}
              title="Open this shelf"
            >
              <strong>{shelf.name}</strong>
              {#if shelf.status !== 'active'}<span class="badge">{shelf.status}</span>{/if}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  <div class="card detail">
    {#if !selected}
      <p class="empty">Select a shelf on the left to see its papers and add or remove members.</p>
    {:else}
      <div class="head">
        <div>
          <span class="muted">Shelf</span>
          <h2>{selected.name}</h2>
        </div>
        <div class="head-actions">
          <label class="access-inline">
            <span class="muted">Access</span>
            <select
              value={selected.access_level}
              on:change={(e) => changeAccess(selected!, e.currentTarget.value as AccessLevel)}
              disabled={loading || !$canManageStructure}
              aria-label="Shelf access level"
              title={$canManageStructure ? 'Who may see and modify this shelf' : INSUFFICIENT_ROLE}
            >
              {#each ACCESS_LEVELS as lvl}<option value={lvl.value}>{lvl.label}</option>{/each}
            </select>
          </label>
          <button type="button" class="secondary" on:click={archive} disabled={loading || !$canManageStructure}
            title={$canManageStructure ? 'Archive this shelf (asks for confirmation)' : INSUFFICIENT_ROLE}>Archive shelf</button>
          <button type="button" class="danger" on:click={removeShelf} disabled={loading || !$canManageStructure}
            title={$canManageStructure ? 'Delete this shelf; papers only here move to the default shelf' : INSUFFICIENT_ROLE}>Delete shelf</button>
        </div>
      </div>

      <div class="add-work">
        <h3>Add a paper to this shelf</h3>
        <input bind:value={workFilter} placeholder="Filter papers by title…" aria-label="Filter papers"
          disabled={!$canManageStructure} />
        <div class="row">
          <select bind:value={pickWorkId} aria-label="Choose a paper" title="Choose a paper to add to this shelf"
            disabled={!$canManageStructure}>
            <option value="">Choose a paper…</option>
            {#each filteredWorks.slice(0, 200) as work (work.id)}
              <option value={work.id}>{work.canonical_title ?? 'Untitled'}{work.year ? ` (${work.year})` : ''}</option>
            {/each}
          </select>
          <button type="button" on:click={addWork} disabled={!pickWorkId || loading || !$canManageStructure}
            title={!$canManageStructure ? INSUFFICIENT_ROLE : pickWorkId ? 'Add the chosen paper' : 'Choose a paper first'}>Add paper</button>
        </div>
        {#if !$canManageStructure}<p class="hintline">{INSUFFICIENT_ROLE} — only librarians and admins can change a shelf’s papers.</p>{:else if !pickWorkId}<p class="hintline">Pick a paper above to enable “Add paper”.</p>{/if}
      </div>

      <h3>Papers in this shelf ({shelfWorks.length})</h3>
      {#if shelfWorks.length === 0}
        <p class="empty">This shelf is empty. Use “Add a paper” above.</p>
      {:else}
        <ul class="member-list">
          {#each shelfWorks as work (work.id)}
            <li>
              <span>{work.canonical_title ?? 'Untitled'}{work.year ? ` · ${work.year}` : ''}</span>
              <button type="button" class="secondary small" on:click={() => removeWork(work.id)}
                disabled={loading || !$canManageStructure}
                title={$canManageStructure ? 'Remove this paper from the shelf' : INSUFFICIENT_ROLE}>Remove</button>
            </li>
          {/each}
        </ul>
      {/if}

      <ExportDialog
        label={`shelf "${selected.name}"`}
        disabled={loading}
        fetchExport={(format, style) =>
          client.exportCitations({ scope_type: 'shelf', scope_id: selected!.id, format, style })}
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
    color: #b45309;
    font-size: 0.8rem;
    margin: 0;
  }

  .shelf-list,
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

  .add-work {
    background: #f4f6f9;
    border: 1px solid #e1e7ee;
    border-radius: 6px;
    margin-top: 0.5rem;
    padding: 0.7rem;
  }

  .add-work input {
    margin-bottom: 0.5rem;
    width: 100%;
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
