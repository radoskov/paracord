<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type Rack,
    type ReadingStatus,
    type Shelf,
    type Tag,
    type Work,
  } from '../api/client';
  import PaperTable from '../components/PaperTable.svelte';
  import WorkDetail from '../components/WorkDetail.svelte';
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

  let newTitle = '';
  let showNew = false;
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

  async function updateStatus(work: Work, status: ReadingStatus): Promise<void> {
    await run(async () => {
      await client.updateWork(work.id, { reading_status: status });
      await loadWorks();
    });
  }

  async function createWork(): Promise<void> {
    await run(async () => {
      const work = await client.createWork({ canonical_title: newTitle });
      newTitle = '';
      showNew = false;
      await loadWorks();
      selected = work;
    }, 'Paper created — add files and metadata on the right');
  }

  function onUpdated(work: Work): void {
    selected = work;
    works = works.map((w) => (w.id === work.id ? work : w));
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
        <button type="button" class="secondary" on:click={() => (showNew = !showNew)}
          title="Create a paper by hand (you can attach a PDF afterwards)">+ New paper</button>
      </div>
      {#if showNew}
        <form class="new" on:submit|preventDefault={createWork}>
          <input bind:value={newTitle} placeholder="Paper title" aria-label="New paper title" />
          <button type="submit" disabled={!newTitle.trim() || loading}>Create</button>
        </form>
      {/if}
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
          onSelect={(w) => {
            selected = w;
          }}
          onStatusChange={updateStatus}
        />
      {/if}
    </div>
  </div>

  <div class="detail-col card">
    {#if selected}
      {#key selected.id}
        <WorkDetail {client} work={selected} {onUpdated} onClose={() => (selected = null)} />
      {/key}
    {:else}
      <p class="empty">Select a paper from the list to view and edit its details, attach a PDF, review metadata, and read it.</p>
    {/if}
  </div>
</section>

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

  .new {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(0, 1fr) auto;
    margin-top: 0.5rem;
  }

  .detail-col {
    min-height: 12rem;
    position: sticky;
    top: 1rem;
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
