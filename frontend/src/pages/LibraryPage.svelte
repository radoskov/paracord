<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';

  import {
    ApiClient,
    type Rack,
    type ReadingStatus,
    type Shelf,
    type Tag,
    type Work,
  } from '../api/client';
  import Modal from '../components/Modal.svelte';
  import PaperTable from '../components/PaperTable.svelte';
  import WorkDetail from '../components/WorkDetail.svelte';
  import { selectedWorkId } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let works: Work[] = [];
  let shelves: Shelf[] = [];
  let racks: Rack[] = [];
  let tags: Tag[] = [];
  let selected: Work | null = null;

  let search = '';
  let statusFilter = '';
  let shelfFilter = '';
  let rackFilter = '';
  let tagFilter = '';

  // New-paper dialog
  let showNew = false;
  let newTitle = '';
  let newDoi = '';
  let newArxiv = '';
  let newUrl = '';

  let loading = false;
  let message = '';

  onMount(async () => {
    await run(async () => {
      [shelves, racks, tags] = await Promise.all([
        client.listShelves(),
        client.listRacks(),
        client.listTags(),
      ]);
    });
    await loadWorks();
    // Restore the previously-open paper when returning to this tab.
    const remembered = get(selectedWorkId);
    if (remembered) selected = works.find((w) => w.id === remembered) ?? null;
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

  async function loadWorks(): Promise<void> {
    await run(async () => {
      works = await client.listWorks({
        q: search,
        readingStatus: statusFilter,
        shelfId: shelfFilter,
        rackId: rackFilter,
        tagId: tagFilter,
      });
      if (selected) selected = works.find((w) => w.id === selected?.id) ?? selected;
    });
  }

  function selectWork(work: Work | null): void {
    selected = work;
    selectedWorkId.set(work?.id ?? null);
  }

  async function updateStatus(work: Work, status: ReadingStatus): Promise<void> {
    await run(async () => {
      await client.updateWork(work.id, { reading_status: status });
      await loadWorks();
    });
  }

  function parseIdentifierUrl(url: string): { doi?: string; arxiv_id?: string } {
    const value = url.trim();
    const arxiv = value.match(/arxiv\.org\/(?:abs|pdf)\/(\d{4}\.\d{4,5}(?:v\d+)?)/i);
    if (arxiv) return { arxiv_id: arxiv[1] };
    const doi = value.match(/(?:doi\.org\/|doi:)?(10\.\d{4,9}\/\S+)/i);
    if (doi) return { doi: doi[1] };
    return {};
  }

  $: canCreate = !!(newTitle.trim() || newDoi.trim() || newArxiv.trim() || newUrl.trim());

  async function createWork(): Promise<void> {
    const fromUrl = newUrl.trim() ? parseIdentifierUrl(newUrl) : {};
    await run(async () => {
      const work = await client.createWork({
        canonical_title: newTitle.trim() || null,
        doi: newDoi.trim() || fromUrl.doi || null,
        arxiv_id: newArxiv.trim() || fromUrl.arxiv_id || null,
      });
      newTitle = newDoi = newArxiv = newUrl = '';
      showNew = false;
      await loadWorks();
      selectWork(work);
    }, 'Paper created — attach a PDF or Enrich it on the right');
  }

  function onUpdated(work: Work): void {
    selected = work;
    works = works.map((w) => (w.id === work.id ? work : w));
  }

  function onDeleted(workId: string): void {
    works = works.filter((w) => w.id !== workId);
    selectWork(null);
    message = 'Paper deleted';
  }
</script>

<section class="layout">
  <div class="list-col">
    <div class="card">
      <form class="filters" on:submit|preventDefault={loadWorks}>
        <input bind:value={search} placeholder="Search title, DOI, arXiv, venue" aria-label="Search" />
        <select bind:value={statusFilter}>
          <option value="">Any status</option>
          <option value="unread">unread</option>
          <option value="skimmed">skimmed</option>
          <option value="reading">reading</option>
          <option value="read">read</option>
          <option value="important">important</option>
          <option value="revisit">revisit</option>
        </select>
        <select bind:value={shelfFilter}>
          <option value="">Any shelf</option>
          {#each shelves as shelf (shelf.id)}<option value={shelf.id}>{shelf.name}</option>{/each}
        </select>
        <select bind:value={rackFilter}>
          <option value="">Any rack</option>
          {#each racks as rack (rack.id)}<option value={rack.id}>{rack.name}</option>{/each}
        </select>
        <select bind:value={tagFilter}>
          <option value="">Any tag</option>
          {#each tags as tag (tag.id)}<option value={tag.id}>{tag.name}</option>{/each}
        </select>
        <button type="submit" disabled={loading} title="Apply search and filters">Search</button>
      </form>
      <div class="bar">
        <span class="muted">{works.length} papers{message ? ` · ${message}` : ''}</span>
        <button type="button" class="secondary" on:click={() => (showNew = true)}
          title="Create a paper by hand (you can attach a PDF afterwards)">+ New paper</button>
      </div>
    </div>

    <div class="card">
      {#if works.length === 0}
        <p class="empty">
          No papers match. Import PDFs or add by arXiv/DOI on the <strong>Import</strong> tab, or use
          “+ New paper”.
        </p>
      {:else}
        <PaperTable
          {works}
          selectedWorkId={selected?.id ?? null}
          onSelect={selectWork}
          onStatusChange={updateStatus}
        />
      {/if}
    </div>
  </div>

  <div class="detail-col card">
    {#if selected}
      {#key selected.id}
        <WorkDetail {client} work={selected} {onUpdated} {onDeleted} onClose={() => selectWork(null)} />
      {/key}
    {:else}
      <p class="empty">Select a paper from the list to view and edit its details, attach a PDF, review metadata, and read it.</p>
    {/if}
  </div>
</section>

{#if showNew}
  <Modal title="New paper" onClose={() => (showNew = false)}>
    <form class="new-form" on:submit|preventDefault={createWork}>
      <p class="muted">Give any of these — title, a DOI, an arXiv id, or a URL. You can attach a PDF and Enrich afterwards.</p>
      <label>Title<input bind:value={newTitle} placeholder="Paper title" /></label>
      <label>DOI<input bind:value={newDoi} placeholder="10.1145/3292500" /></label>
      <label>arXiv id<input bind:value={newArxiv} placeholder="1706.03762" /></label>
      <label>URL<input bind:value={newUrl} placeholder="https://arxiv.org/abs/… or https://doi.org/…" /></label>
      <div class="actions">
        <button type="submit" disabled={!canCreate || loading}>Create paper</button>
        <button type="button" class="secondary" on:click={() => (showNew = false)}>Cancel</button>
      </div>
      {#if !canCreate}<p class="hintline">Enter at least one identifier to enable “Create paper”.</p>{/if}
    </form>
  </Modal>
{/if}

<style>
  .layout {
    align-items: start;
    display: grid;
    gap: 1rem;
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  }

  .list-col {
    display: grid;
    gap: 1rem;
  }

  .filters {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(10rem, 1.6fr) repeat(4, minmax(7rem, 1fr)) auto;
  }

  .bar {
    align-items: center;
    display: flex;
    justify-content: space-between;
    margin-top: 0.6rem;
  }

  .new-form {
    display: grid;
    gap: 0.6rem;
  }

  .actions {
    display: flex;
    gap: 0.5rem;
  }

  .detail-col {
    min-height: 12rem;
    position: sticky;
    top: 5rem;
  }

  @media (max-width: 1000px) {
    .layout {
      grid-template-columns: 1fr;
    }

    .filters {
      grid-template-columns: 1fr 1fr;
    }

    .detail-col {
      position: static;
    }
  }
</style>
