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
  import ExportDialog from '../components/ExportDialog.svelte';
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
  let searchMode: 'metadata' | 'semantic' = 'metadata';
  let statusFilter = '';
  let shelfFilter = '';
  let rackFilter = '';
  let tagFilter = '';
  let pdfFilter = ''; // '' | 'yes' | 'no'
  let refsFilter = ''; // '' | 'yes' | 'no'
  const MISSING_FIELDS = ['title', 'abstract', 'year', 'venue', 'doi', 'arxiv_id'];
  let missing: string[] = [];
  let showMoreFilters = false;

  // multi-select
  let selectedIds: string[] = [];
  let batchStatus: ReadingStatus | '' = '';

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

  function structuredQuery() {
    return {
      readingStatus: statusFilter,
      shelfId: shelfFilter,
      rackId: rackFilter,
      tagId: tagFilter,
      hasPdf: pdfFilter ? pdfFilter === 'yes' : undefined,
      hasReferences: refsFilter ? refsFilter === 'yes' : undefined,
      missing: missing.length ? missing : undefined,
    };
  }

  async function loadWorks(): Promise<void> {
    await run(async () => {
      if (searchMode === 'semantic' && search.trim()) {
        // Rank by semantic similarity, then intersect with the active structured filters.
        const [ranked, filtered] = await Promise.all([
          client.semanticSearch(search.trim(), 50),
          client.listWorks(structuredQuery()),
        ]);
        const byId = new Map(filtered.map((w) => [w.id, w]));
        works = ranked.items.map((i) => byId.get(i.work_id)).filter((w): w is Work => !!w);
        if (works.length === 0 && ranked.items.length === 0) {
          message =
            'No semantic matches. Embeddings are built on first search; if this stays empty, ' +
            'no papers have indexable text yet.';
        }
      } else {
        works = await client.listWorks({ q: search, ...structuredQuery() });
      }
      // Drop selections that fell out of the result set.
      const present = new Set(works.map((w) => w.id));
      selectedIds = selectedIds.filter((id) => present.has(id));
      if (selected) selected = works.find((w) => w.id === selected?.id) ?? selected;
    });
  }

  function resetFilters(): void {
    statusFilter = shelfFilter = rackFilter = tagFilter = pdfFilter = refsFilter = '';
    missing = [];
    search = '';
    void loadWorks();
  }

  function toggleMissing(field: string): void {
    missing = missing.includes(field)
      ? missing.filter((f) => f !== field)
      : [...missing, field];
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

  // --- multi-select ---
  function toggleSelect(id: string): void {
    selectedIds = selectedIds.includes(id)
      ? selectedIds.filter((x) => x !== id)
      : [...selectedIds, id];
  }

  function toggleSelectAll(checked: boolean): void {
    selectedIds = checked ? works.map((w) => w.id) : [];
  }

  async function batchDelete(): Promise<void> {
    if (!window.confirm(`Delete ${selectedIds.length} paper(s)? Their files stay in the library.`))
      return;
    const ids = [...selectedIds];
    await run(async () => {
      for (const id of ids) await client.deleteWork(id);
      if (selected && ids.includes(selected.id)) selectWork(null);
      selectedIds = [];
      await loadWorks();
    }, `Deleted ${ids.length} paper(s)`);
  }

  async function batchReextract(): Promise<void> {
    const ids = [...selectedIds];
    await run(async () => {
      let queued = 0;
      let unavailable = false;
      for (const id of ids) {
        const files = await client.listWorkFiles(id);
        for (const file of files) {
          try {
            await client.extractFile(file.id);
            queued += 1;
          } catch {
            unavailable = true;
          }
        }
      }
      message = unavailable
        ? `Queued ${queued} extraction(s); some failed (queue unavailable?).`
        : queued
          ? `Queued ${queued} extraction(s) — watch the Jobs tab.`
          : 'Nothing to extract: the selected papers have no attached files.';
    });
  }

  async function batchSetStatus(): Promise<void> {
    if (!batchStatus) return;
    const ids = [...selectedIds];
    const status = batchStatus;
    await run(async () => {
      for (const id of ids) await client.updateWork(id, { reading_status: status });
      batchStatus = '';
      await loadWorks();
    }, `Set ${ids.length} paper(s) to “${status}”`);
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
  $: allSelected = works.length > 0 && selectedIds.length === works.length;

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
        <div class="search-row">
          <input
            bind:value={search}
            placeholder={searchMode === 'semantic'
              ? 'Describe the topic / keywords…'
              : 'Search title, DOI, arXiv, venue'}
            aria-label="Search"
          />
          <select bind:value={searchMode} title="Search mode" aria-label="Search mode">
            <option value="metadata">metadata</option>
            <option value="semantic">semantic</option>
          </select>
          <button type="submit" disabled={loading} title="Apply search and filters">Search</button>
        </div>
        <div class="filter-row">
          <select bind:value={statusFilter} on:change={loadWorks} aria-label="Reading status">
            <option value="">Any status</option>
            {#each ['unread', 'skimmed', 'reading', 'read', 'important', 'revisit'] as s}
              <option value={s}>{s}</option>
            {/each}
          </select>
          <select bind:value={shelfFilter} on:change={loadWorks} aria-label="Shelf">
            <option value="">Any shelf</option>
            {#each shelves as shelf (shelf.id)}<option value={shelf.id}>{shelf.name}</option>{/each}
          </select>
          <select bind:value={rackFilter} on:change={loadWorks} aria-label="Rack">
            <option value="">Any rack</option>
            {#each racks as rack (rack.id)}<option value={rack.id}>{rack.name}</option>{/each}
          </select>
          <select bind:value={tagFilter} on:change={loadWorks} aria-label="Tag">
            <option value="">Any tag</option>
            {#each tags as tag (tag.id)}<option value={tag.id}>{tag.name}</option>{/each}
          </select>
          <button type="button" class="secondary" on:click={() => (showMoreFilters = !showMoreFilters)}
            title="Filter by extraction / metadata completeness">
            {showMoreFilters ? 'Fewer filters' : 'More filters'}
          </button>
        </div>
        {#if showMoreFilters}
          <div class="more-filters">
            <label class="inline">PDF
              <select bind:value={pdfFilter} on:change={loadWorks}>
                <option value="">any</option>
                <option value="yes">has file</option>
                <option value="no">no file</option>
              </select>
            </label>
            <label class="inline">References
              <select bind:value={refsFilter} on:change={loadWorks}>
                <option value="">any</option>
                <option value="yes">extracted</option>
                <option value="no">none</option>
              </select>
            </label>
            <span class="missing">
              <span class="missing-label">Missing:</span>
              {#each MISSING_FIELDS as field}
                <button
                  type="button"
                  class="chip"
                  class:on={missing.includes(field)}
                  on:click={() => {
                    toggleMissing(field);
                    loadWorks();
                  }}
                >{field}</button>
              {/each}
            </span>
            <button type="button" class="secondary" on:click={resetFilters}>Reset</button>
          </div>
        {/if}
      </form>
      <div class="bar">
        <span class="muted">{works.length} papers{message ? ` · ${message}` : ''}</span>
        <button type="button" class="secondary" on:click={() => (showNew = true)}
          title="Create a paper by hand (you can attach a PDF afterwards)">+ New paper</button>
      </div>
      {#if selectedIds.length > 0}
        <div class="batch">
          <strong>{selectedIds.length} selected</strong>
          <button type="button" class="secondary danger-btn" on:click={batchDelete} disabled={loading}>Delete</button>
          <button type="button" class="secondary" on:click={batchReextract} disabled={loading}
            title="Queue GROBID extraction for every attached file of the selected papers">Re-extract</button>
          <label class="inline">Set status
            <select bind:value={batchStatus} on:change={batchSetStatus} disabled={loading}>
              <option value="">…</option>
              {#each ['unread', 'skimmed', 'reading', 'read', 'important', 'revisit'] as s}
                <option value={s}>{s}</option>
              {/each}
            </select>
          </label>
          <ExportDialog
            label="selection"
            fetchExport={(format) =>
              client.exportCitations({ scope_type: 'selection', work_ids: selectedIds, format })}
          />
          <button type="button" class="link" on:click={() => (selectedIds = [])}>Clear</button>
        </div>
      {/if}
    </div>

    <div class="card list-scroll">
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
          selectable
          {selectedIds}
          {allSelected}
          onToggleSelect={toggleSelect}
          onToggleAll={toggleSelectAll}
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
    align-items: stretch;
    display: grid;
    gap: 1rem;
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
    /* Fill the viewport below the header/hint so each column scrolls on its own. */
    height: calc(100dvh - 7rem);
    min-height: 22rem;
  }

  .list-col {
    display: grid;
    gap: 1rem;
    grid-template-rows: auto minmax(0, 1fr);
    min-height: 0;
  }

  .list-scroll {
    min-height: 0;
    overflow-y: auto;
  }

  .detail-col {
    min-height: 0;
    overflow-y: auto;
  }

  .filters {
    display: grid;
    gap: 0.5rem;
  }

  .search-row {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(8rem, 1fr) auto auto;
  }

  .filter-row {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: repeat(4, minmax(6rem, 1fr)) auto;
  }

  .more-filters {
    align-items: center;
    background: #f4f6f9;
    border: 1px solid #e1e7ee;
    border-radius: 6px;
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    padding: 0.5rem 0.6rem;
  }

  .inline {
    align-items: center;
    color: #44515f;
    display: flex;
    flex-direction: row;
    gap: 0.35rem;
  }

  .missing {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .missing-label {
    color: #526070;
    font-size: 0.82rem;
    font-weight: 600;
  }

  .chip {
    background: #fff;
    border: 1px solid var(--pg-border);
    border-radius: 999px;
    color: #44515f;
    font-size: 0.78rem;
    min-height: 1.9rem;
    padding: 0.15rem 0.6rem;
  }

  .chip.on {
    background: #2d3e50;
    border-color: #2d3e50;
    color: #fff;
  }

  .bar {
    align-items: center;
    display: flex;
    justify-content: space-between;
    margin-top: 0.6rem;
  }

  .batch {
    align-items: center;
    background: #eef4ef;
    border: 1px solid #cfe3d6;
    border-radius: 6px;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.6rem;
    padding: 0.45rem 0.6rem;
  }

  .danger-btn {
    border-color: #f1b0a8;
    color: #b3261e;
  }

  .link {
    background: none;
    border: none;
    color: #2563eb;
    cursor: pointer;
    min-height: auto;
    padding: 0;
    text-decoration: underline;
  }

  .new-form {
    display: grid;
    gap: 0.6rem;
  }

  .actions {
    display: flex;
    gap: 0.5rem;
  }

  @media (max-width: 1000px) {
    .layout {
      grid-template-columns: 1fr;
      height: auto;
    }

    .list-scroll,
    .detail-col {
      overflow-y: visible;
    }

    .search-row,
    .filter-row {
      grid-template-columns: 1fr 1fr;
    }
  }
</style>
