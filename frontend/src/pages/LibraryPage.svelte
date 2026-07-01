<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { get } from 'svelte/store';

  import {
    ApiClient,
    type Rack,
    type ReadingStatus,
    type SavedFilter,
    type Shelf,
    type Tag,
    type Work,
    type WorkSortKey,
  } from '../api/client';
  import ColumnPicker from '../components/ColumnPicker.svelte';
  import ExportDialog from '../components/ExportDialog.svelte';
  import Modal from '../components/Modal.svelte';
  import PaperTable from '../components/PaperTable.svelte';
  import ShelfPicker from '../components/ShelfPicker.svelte';
  import WorkDetail from '../components/WorkDetail.svelte';
  import {
    type ColumnId,
    type ColumnPrefs,
    loadColumnPrefs,
    normalizeColumnPrefs,
    saveColumnPrefs,
    visibleColumnDefs,
  } from '../lib/columns';
  import {
    pendingLibraryOpen,
    pendingLibrarySearch,
    selectedPaperIds,
    selectedWorkId,
  } from '../lib/selection';
  import { canEdit, canManageStructure, canModifyWork, currentUser, INSUFFICIENT_ROLE } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let works: Work[] = [];
  let shelves: Shelf[] = [];
  let racks: Rack[] = [];
  let tags: Tag[] = [];
  let savedFilters: SavedFilter[] = [];
  // Bound to the "Apply saved filter…" dropdown; reset to '' after applying so it stays a prompt.
  let applyFilterId = '';
  let selected: Work | null = null;

  let search = '';
  let searchMode: 'metadata' | 'semantic' = 'metadata';
  // Phase B2: true when the last semantic search silently fell back to the built-in baseline
  // embedder (a heavier provider was configured but unavailable).
  let semanticDegraded = false;
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
  let showBatchPutInto = false;
  let batchPutIntoShelfId = '';

  // New-paper dialog
  let showNew = false;
  let newTitle = '';
  let newDoi = '';
  let newArxiv = '';
  let newUrl = '';

  let loading = false;
  let message = '';

  // Column preferences (which columns show, in what order) + current sort. localStorage is applied
  // synchronously on mount (no flash); the backend prefs file is the durable source we reconcile to.
  let columnPrefs: ColumnPrefs = loadColumnPrefs();
  let showColumns = false;
  let savePrefsTimer: ReturnType<typeof setTimeout> | null = null;
  $: sortKey = columnPrefs.sort.key;
  $: sortOrder = columnPrefs.sort.order;
  $: visibleColumns = visibleColumnDefs(columnPrefs);

  // Default sort direction when a user first clicks a column: newest/highest-first for date/numeric
  // columns, A→Z for text columns.
  const DESC_FIRST: WorkSortKey[] = ['added_at', 'updated_at', 'year'];

  onMount(async () => {
    await run(async () => {
      [shelves, racks, tags, savedFilters] = await Promise.all([
        client.listShelves(),
        client.listRacks(),
        client.listTags(),
        client.listSavedFilters().catch(() => [] as SavedFilter[]),
      ]);
    });
    // Reconcile with the backend (durable source); tolerate failure (keep the localStorage copy).
    try {
      const remote = await client.getPreferences();
      if (remote?.library_columns) {
        columnPrefs = normalizeColumnPrefs(remote.library_columns);
        saveColumnPrefs(columnPrefs); // write the merged result back to localStorage
      }
    } catch {
      /* offline / read-only backend — the localStorage copy stands */
    }
    await loadWorks();
    const remembered = get(selectedWorkId);
    if (remembered) selected = works.find((w) => w.id === remembered) ?? null;
  });

  // Persist column prefs: localStorage immediately (instant + offline-safe), backend debounced.
  function persistColumnPrefs(): void {
    saveColumnPrefs(columnPrefs);
    if (savePrefsTimer) clearTimeout(savePrefsTimer);
    savePrefsTimer = setTimeout(() => {
      void client
        .putPreferences({ library_columns: columnPrefs })
        .catch(() => {
          // Read-only backend (503) or offline: localStorage already holds the change.
          message = 'Column layout saved locally only (server storage is read-only).';
        });
    }, 600);
  }

  function applyColumns(next: { order: ColumnId[]; visible: ColumnId[] }): void {
    columnPrefs = normalizeColumnPrefs({ ...columnPrefs, ...next });
    persistColumnPrefs();
  }

  function handleSort(key: WorkSortKey): void {
    const order: 'asc' | 'desc' =
      key === columnPrefs.sort.key
        ? columnPrefs.sort.order === 'asc'
          ? 'desc'
          : 'asc'
        : DESC_FIRST.includes(key)
          ? 'desc'
          : 'asc';
    columnPrefs = { ...columnPrefs, sort: { key, order } };
    persistColumnPrefs();
    void loadWorks();
  }

  // A keyword chip (or other tab) requested a Library search — consume it once and run it.
  const unsubscribePendingSearch = pendingLibrarySearch.subscribe((req) => {
    if (!req) return;
    search = req.query;
    searchMode = req.mode;
    pendingLibrarySearch.set(null);
    void loadWorks();
  });
  onDestroy(unsubscribePendingSearch);
  // Open a paper requested from another tab (e.g. a search result). Fetches it if it isn't in the
  // current filtered list. Resets the request so re-clicking the same paper works again.
  const unsubscribePendingOpen = pendingLibraryOpen.subscribe((workId) => {
    if (!workId) return;
    pendingLibraryOpen.set(null);
    void onSelectWork(workId);
  });
  onDestroy(unsubscribePendingOpen);
  onDestroy(() => {
    if (savePrefsTimer) clearTimeout(savePrefsTimer);
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

  // Snake_case params for the saved-filter API (mirrors the backend SavedFilterParams schema).
  function savedFilterParams() {
    return {
      reading_status: statusFilter || null,
      shelf_id: shelfFilter || null,
      rack_id: rackFilter || null,
      tag_id: tagFilter || null,
      has_pdf: pdfFilter ? pdfFilter === 'yes' : null,
      has_references: refsFilter ? refsFilter === 'yes' : null,
      missing: [...missing],
    };
  }

  // --- Saved filters: save the current search + filters, apply a stored one, delete one ---
  async function saveCurrentFilter(): Promise<void> {
    const name = window.prompt('Name this saved filter:');
    if (!name || !name.trim()) return;
    await run(async () => {
      const created = await client.createSavedFilter({
        name: name.trim(),
        search_mode: searchMode,
        query_text: search,
        params: savedFilterParams(),
      });
      savedFilters = [...savedFilters, created].sort((a, b) => a.name.localeCompare(b.name));
    }, `Saved filter “${name.trim()}”`);
  }

  function applySavedFilter(filter: SavedFilter): void {
    search = filter.query_text ?? '';
    searchMode = filter.search_mode;
    const p = filter.params ?? {};
    statusFilter = p.reading_status ?? '';
    shelfFilter = p.shelf_id ?? '';
    rackFilter = p.rack_id ?? '';
    tagFilter = p.tag_id ?? '';
    pdfFilter = p.has_pdf === true ? 'yes' : p.has_pdf === false ? 'no' : '';
    refsFilter = p.has_references === true ? 'yes' : p.has_references === false ? 'no' : '';
    missing = [...(p.missing ?? [])];
    void loadWorks();
  }

  function onApplyFilterChange(event: Event): void {
    const id = (event.currentTarget as HTMLSelectElement).value;
    const filter = savedFilters.find((f) => f.id === id);
    if (filter) applySavedFilter(filter);
    applyFilterId = ''; // reset so the select stays on the "Apply saved filter…" prompt
  }

  async function deleteSavedFilter(filter: SavedFilter): Promise<void> {
    if (!window.confirm(`Delete saved filter “${filter.name}”?`)) return;
    await run(async () => {
      await client.deleteSavedFilter(filter.id);
      savedFilters = savedFilters.filter((f) => f.id !== filter.id);
    }, `Deleted saved filter “${filter.name}”`);
  }

  // Re-sort an already-loaded set by the active column (used for the semantic-ranked set, which
  // can't be sorted server-side without losing the similarity ranking).
  function sortWorksClientSide(items: Work[]): Work[] {
    const { key, order } = columnPrefs.sort;
    const dir = order === 'asc' ? 1 : -1;
    const value = (w: Work): string | number => {
      switch (key) {
        case 'title':
          return (w.canonical_title ?? '').toLowerCase();
        case 'year':
          return w.year ?? -Infinity;
        case 'venue':
          return (w.venue ?? '').toLowerCase();
        case 'reading_status':
          return w.reading_status;
        case 'added_at':
          return w.created_at ?? '';
        case 'updated_at':
        default:
          return w.updated_at ?? '';
      }
    };
    return [...items].sort((a, b) => {
      const av = value(a);
      const bv = value(b);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0; // stable id tiebreaker (mirrors backend)
    });
  }

  async function loadWorks(): Promise<void> {
    await run(async () => {
      if (searchMode === 'semantic' && search.trim()) {
        // Rank by semantic similarity, then intersect with the active structured filters.
        const [ranked, filtered] = await Promise.all([
          client.semanticSearch(search.trim(), 50),
          client.listWorks(structuredQuery()),
        ]);
        semanticDegraded = ranked.degraded === true;
        const byId = new Map(filtered.map((w) => [w.id, w]));
        const ordered = ranked.items.map((i) => byId.get(i.work_id)).filter((w): w is Work => !!w);
        // Re-sort the ranked set client-side so the chosen column ordering applies in semantic mode.
        works = sortWorksClientSide(ordered);
        if (works.length === 0 && ranked.items.length === 0) {
          message =
            'No semantic matches. Embeddings are built on first search; if this stays empty, ' +
            'no papers have indexable text yet.';
        }
      } else {
        semanticDegraded = false;
        works = await client.listWorks({
          q: search,
          ...structuredQuery(),
          sort: columnPrefs.sort.key,
          order: columnPrefs.sort.order,
        });
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

  // Switch the open paper to a related one by id. Prefer the already-loaded list; fall back to a
  // direct fetch when the related paper isn't in the current (filtered) results.
  async function onSelectWork(workId: string): Promise<void> {
    const inList = works.find((w) => w.id === workId);
    if (inList) {
      selectWork(inList);
      return;
    }
    await run(async () => {
      selectWork(await client.getWork(workId));
    });
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

  async function batchPutInto(): Promise<void> {
    if (!batchPutIntoShelfId) return;
    const shelfId = batchPutIntoShelfId;
    const ids = [...selectedIds];
    await run(async () => {
      for (const id of ids) await client.addWorkToShelf(shelfId, id);
      showBatchPutInto = false;
      batchPutIntoShelfId = '';
    }, `Added ${ids.length} paper(s) to shelf`);
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
  // Mirror the multi-selection into the cross-tab store so the Insights "Selected papers" graph
  // scope can operate on the current selection.
  $: selectedPaperIds.set(selectedIds);
  // Batch actions modify papers, so only enable them when the user may modify EVERY selected paper
  // (contributor → own papers only; editor+ → any visible paper). The server enforces this too.
  $: canModifySelected =
    selectedIds.length > 0 &&
    selectedIds.every((id) => {
      const w = works.find((work) => work.id === id);
      return !!w && canModifyWork($currentUser, w);
    });
  $: batchHint = canModifySelected
    ? null
    : 'You can only run this on papers you may modify (your own papers, or any paper if you are an editor or higher).';

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

  function onImported(): void {
    // A reference was imported into the library as a new paper — reload the list so it appears.
    void loadWorks();
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
              : 'Search… e.g. transformer author:doe year:>=2020 has:pdf tag:ml'}
            aria-label="Search"
          />
          <select bind:value={searchMode} title="Search mode" aria-label="Search mode">
            <option value="metadata">metadata</option>
            <option value="semantic">semantic</option>
          </select>
          <button type="submit" disabled={loading} title="Apply search and filters">Search</button>
        </div>
        {#if semanticDegraded}
          <p class="degraded-hint" role="status">Semantic search is using the built-in baseline (sentence-transformers not configured).</p>
        {/if}
        <div class="filter-row">
          <select bind:value={statusFilter} on:change={loadWorks} aria-label="Reading status"
            title="Filter the list by reading status">
            <option value="">Any status</option>
            {#each ['unread', 'skimmed', 'reading', 'read', 'important', 'revisit'] as s}
              <option value={s}>{s}</option>
            {/each}
          </select>
          <select bind:value={shelfFilter} on:change={loadWorks} aria-label="Shelf"
            title="Filter the list by shelf">
            <option value="">Any shelf</option>
            {#each shelves as shelf (shelf.id)}<option value={shelf.id}>{shelf.name}</option>{/each}
          </select>
          <select bind:value={rackFilter} on:change={loadWorks} aria-label="Rack"
            title="Filter the list by rack">
            <option value="">Any rack</option>
            {#each racks as rack (rack.id)}<option value={rack.id}>{rack.name}</option>{/each}
          </select>
          <select bind:value={tagFilter} on:change={loadWorks} aria-label="Tag"
            title="Filter the list by tag">
            <option value="">Any tag</option>
            {#each tags as tag (tag.id)}<option value={tag.id}>{tag.name}</option>{/each}
          </select>
          <button type="button" class="secondary" on:click={() => (showMoreFilters = !showMoreFilters)}
            title="Filter by extraction / metadata completeness">
            {showMoreFilters ? 'Fewer filters' : 'More filters'}
          </button>
        </div>
        <div class="saved-row">
          <select
            bind:value={applyFilterId}
            on:change={onApplyFilterChange}
            aria-label="Apply saved filter"
            title="Apply one of your saved filters to the search and filters above"
          >
            <option value="">Apply saved filter…</option>
            {#each savedFilters as filter (filter.id)}
              <option value={filter.id}>{filter.name}</option>
            {/each}
          </select>
          <button
            type="button"
            class="secondary"
            on:click={saveCurrentFilter}
            title="Save the current search and filters as a reusable saved filter"
          >Save current filter</button>
          {#if savedFilters.length > 0}
            <span class="saved-chips" aria-label="Delete saved filters">
              {#each savedFilters as filter (filter.id)}
                <button
                  type="button"
                  class="chip saved-chip"
                  on:click={() => deleteSavedFilter(filter)}
                  title={`Delete saved filter “${filter.name}”`}
                >{filter.name} ✕</button>
              {/each}
            </span>
          {/if}
        </div>
        {#if showMoreFilters}
          <div class="more-filters">
            <label class="inline">PDF
              <select bind:value={pdfFilter} on:change={loadWorks}
                title="Filter by whether the paper has an attached PDF">
                <option value="">any</option>
                <option value="yes">has file</option>
                <option value="no">no file</option>
              </select>
            </label>
            <label class="inline">References
              <select bind:value={refsFilter} on:change={loadWorks}
                title="Filter by whether references have been extracted">
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
                  title={missing.includes(field)
                    ? `Stop filtering on missing ${field}`
                    : `Show only papers missing ${field}`}
                  on:click={() => {
                    toggleMissing(field);
                    loadWorks();
                  }}
                >{field}</button>
              {/each}
            </span>
            <button type="button" class="secondary" on:click={resetFilters}
              title="Clear the search box and all filters">Reset</button>
          </div>
        {/if}
      </form>
      <div class="bar">
        <span class="muted">{works.length} papers{message ? ` · ${message}` : ''}</span>
        <span class="bar-actions">
          <button type="button" class="secondary" on:click={() => (showColumns = true)}
            title="Choose which columns show and their order">Columns</button>
          <button type="button" class="secondary" on:click={() => (showNew = true)} disabled={!$canEdit}
            title={$canEdit ? 'Create a paper by hand (you can attach a PDF afterwards)' : INSUFFICIENT_ROLE}>+ New paper</button>
        </span>
      </div>
      {#if selectedIds.length > 0}
        <div class="batch">
          <strong>{selectedIds.length} selected</strong>
          <button type="button" class="secondary danger-btn" on:click={batchDelete} disabled={loading || !canModifySelected}
            title={canModifySelected ? 'Delete the selected papers (their files stay in the library)' : batchHint}>Delete</button>
          <button type="button" class="secondary" on:click={batchReextract} disabled={loading || !canModifySelected}
            title={canModifySelected ? 'Queue GROBID extraction for every attached file of the selected papers' : batchHint}>Re-extract</button>
          <button type="button" class="secondary" on:click={() => (showBatchPutInto = true)}
            disabled={loading || !$canManageStructure}
            title={$canManageStructure ? 'Add all selected papers to a shelf' : INSUFFICIENT_ROLE}>Put all into…</button>
          <label class="inline">Set status
            <select bind:value={batchStatus} on:change={batchSetStatus} disabled={loading || !canModifySelected}
              title={canModifySelected ? 'Set the reading status for all selected papers' : batchHint}>
              <option value="">…</option>
              {#each ['unread', 'skimmed', 'reading', 'read', 'important', 'revisit'] as s}
                <option value={s}>{s}</option>
              {/each}
            </select>
          </label>
          <ExportDialog
            label="selection"
            fetchExport={(format, style) =>
              client.exportCitations({
                scope_type: 'selection',
                work_ids: selectedIds,
                format,
                style,
              })}
            fetchStyles={() => client.listCitationStyles()}
          />
          <button type="button" class="link" on:click={() => (selectedIds = [])}
            title="Clear the current selection">Clear</button>
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
          columns={visibleColumns}
          sortable
          {sortKey}
          {sortOrder}
          onSort={handleSort}
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
        <WorkDetail {client} work={selected} {onUpdated} {onDeleted} {onImported} {onSelectWork} onClose={() => selectWork(null)} />
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
        <button type="submit" disabled={!canCreate || loading || !$canEdit}
          title={!$canEdit
            ? INSUFFICIENT_ROLE
            : canCreate
              ? 'Create the paper from the details above'
              : 'Enter a title, DOI, arXiv id or URL first'}>Create paper</button>
        <button type="button" class="secondary" on:click={() => (showNew = false)}
          title="Discard and close without creating">Cancel</button>
      </div>
      {#if !canCreate}<p class="hintline">Enter at least one identifier to enable “Create paper”.</p>{/if}
    </form>
  </Modal>
{/if}

{#if showColumns}
  <ColumnPicker
    order={columnPrefs.order}
    visible={columnPrefs.visible}
    onApply={applyColumns}
    onClose={() => (showColumns = false)}
  />
{/if}

{#if showBatchPutInto}
  <Modal title="Put into a shelf" onClose={() => (showBatchPutInto = false)}>
    <div class="putinto">
      <p class="muted">Add {selectedIds.length} selected paper(s) to a shelf.</p>
      <ShelfPicker {client} bind:value={batchPutIntoShelfId} modifiableOnly />
      <div class="putinto-actions">
        <button type="button" class="secondary" on:click={() => (showBatchPutInto = false)}
          title="Close without adding">Cancel</button>
        <button type="button" on:click={batchPutInto} disabled={loading || !batchPutIntoShelfId}
          title={batchPutIntoShelfId ? 'Add the selected papers to the chosen shelf' : 'Choose a shelf first'}>Add</button>
      </div>
    </div>
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

  .degraded-hint {
    margin: 0.25rem 0 0;
    padding: 0.4rem 0.6rem;
    border-radius: 0.375rem;
    background: #fef3c7;
    color: #78350f;
    font-size: 0.85rem;
  }

  .filter-row {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: repeat(4, minmax(6rem, 1fr)) auto;
  }

  .saved-row {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .saved-chips {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .saved-chip {
    cursor: pointer;
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

  .bar-actions {
    display: flex;
    gap: 0.5rem;
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

  .putinto {
    display: grid;
    gap: 0.7rem;
  }

  .putinto-actions {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
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
