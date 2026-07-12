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
  // Server-controlled pagination (D18). `page` is the 1-based current page; `totalPages`/`total`
  // come from the response envelope. Semantic search is ranked separately and isn't paginated.
  let page = 1;
  let totalPages = 1;
  let totalWorks = 0;
  let perPage = 0;
  let shelves: Shelf[] = [];
  let racks: Rack[] = [];
  let tags: Tag[] = [];
  let savedFilters: SavedFilter[] = [];
  // Bound to the "Apply saved filter…" dropdown; reset to '' after applying so it stays a prompt.
  let applyFilterId = '';
  let selected: Work | null = null;

  let search = '';
  let searchMode: 'metadata' | 'semantic' | 'hybrid' = 'metadata';
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
  // Bulk-action dropdown (issue 3): one action + Go, keeping the toolbar uncluttered. "Set metadata"
  // reveals a field picker for the best-source apply.
  let batchAction = '';
  let batchMetaField = 'title';

  // New-paper dialog
  let showNew = false;
  let newTitle = '';
  let newDoi = '';
  let newArxiv = '';
  let newUrl = '';

  let loading = false;
  let message = '';

  // "Jump to open paper" (L3): scroll the open paper's row into view + briefly flash it. `flashWorkId`
  // drives PaperTable's flash animation; `listScrollEl` is the scroll container we query the row in.
  let flashWorkId: string | null = null;
  let listScrollEl: HTMLElement | null = null;
  let flashTimer: ReturnType<typeof setTimeout> | null = null;

  function jumpToSelected(): void {
    if (!selected || !listScrollEl) return;
    const row = listScrollEl.querySelector<HTMLElement>(`[data-work-id="${selected.id}"]`);
    if (!row) {
      // The open paper was opened from another tab / lives on a different page, so its row isn't in
      // the current result set. Point the user at how to bring it into view rather than jumping.
      message = 'The open paper isn’t on this page — clear filters or change page to find it.';
      return;
    }
    row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    flashWorkId = selected.id;
    if (flashTimer) clearTimeout(flashTimer);
    flashTimer = setTimeout(() => (flashWorkId = null), 1600);
  }

  // Column preferences (which columns show, in what order) + current sort. localStorage is applied
  // synchronously on mount (no flash); the backend prefs file is the durable source we reconcile to.
  let columnPrefs: ColumnPrefs = loadColumnPrefs();
  let showColumns = false;
  let savePrefsTimer: ReturnType<typeof setTimeout> | null = null;
  $: sortKey = columnPrefs.sort.key;
  $: sortOrder = columnPrefs.sort.order;
  $: visibleColumns = visibleColumnDefs(columnPrefs);

  // Draggable divider between the paper list and paper view columns, persisted per-browser (the
  // table's own column widths live in lib/columns.ts — this is the outer list/detail split).
  const LIST_COL_PCT_KEY = 'paracord.library.listColPct';
  const DEFAULT_LIST_COL_PCT = 52.4; // matches the previous fixed 1.1fr / 1fr grid ratio
  let listColPct = loadListColPct();
  let layoutEl: HTMLElement;
  let draggingSplitter = false;

  function loadListColPct(): number {
    try {
      const stored = Number(localStorage.getItem(LIST_COL_PCT_KEY));
      if (Number.isFinite(stored) && stored >= 25 && stored <= 75) return stored;
    } catch {
      /* localStorage unavailable (private mode) — fall back to the default */
    }
    return DEFAULT_LIST_COL_PCT;
  }

  function saveListColPct(pct: number): void {
    try {
      localStorage.setItem(LIST_COL_PCT_KEY, String(pct));
    } catch {
      /* non-fatal — the in-memory value still applies for this session */
    }
  }

  function startSplitterDrag(event: MouseEvent): void {
    event.preventDefault();
    draggingSplitter = true;
    window.addEventListener('mousemove', onSplitterDrag);
    window.addEventListener('mouseup', stopSplitterDrag);
  }

  function onSplitterDrag(event: MouseEvent): void {
    if (!draggingSplitter || !layoutEl) return;
    const rect = layoutEl.getBoundingClientRect();
    listColPct = Math.min(75, Math.max(25, ((event.clientX - rect.left) / rect.width) * 100));
  }

  function stopSplitterDrag(): void {
    if (!draggingSplitter) return;
    draggingSplitter = false;
    window.removeEventListener('mousemove', onSplitterDrag);
    window.removeEventListener('mouseup', stopSplitterDrag);
    saveListColPct(listColPct);
  }

  function nudgeSplitter(event: KeyboardEvent): void {
    if (event.key === 'ArrowLeft') listColPct = Math.max(25, listColPct - 2);
    else if (event.key === 'ArrowRight') listColPct = Math.min(75, listColPct + 2);
    else return;
    event.preventDefault();
    saveListColPct(listColPct);
  }

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
    reload();
  }

  // A keyword chip (or other tab) requested a Library search — consume it once and run it.
  const unsubscribePendingSearch = pendingLibrarySearch.subscribe((req) => {
    if (!req) return;
    search = req.query;
    searchMode = req.mode;
    pendingLibrarySearch.set(null);
    reload();
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
    if (flashTimer) clearTimeout(flashTimer);
    window.removeEventListener('mousemove', onSplitterDrag);
    window.removeEventListener('mouseup', stopSplitterDrag);
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

  // Run an async op over many ids with bounded concurrency (D16) instead of one serial round-trip
  // per id. Uses Promise.allSettled per chunk so a single failure doesn't abort the rest; returns
  // how many failed so callers can surface it rather than silently swallowing errors.
  async function runBatched<T>(
    items: T[],
    op: (item: T) => Promise<unknown>,
    size = 6,
  ): Promise<number> {
    let failed = 0;
    for (let i = 0; i < items.length; i += size) {
      const results = await Promise.allSettled(items.slice(i, i + size).map(op));
      failed += results.filter((r) => r.status === 'rejected').length;
    }
    return failed;
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
    reload();
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
        case 'file_count':
          return w.file_count ?? 0;
        case 'reference_count':
          return w.reference_count ?? 0;
        case 'citation_count':
          return w.citation_count ?? -Infinity;
        case 'local_reference_count':
          return w.local_reference_count ?? 0;
        case 'local_citation_count':
          return w.local_citation_count ?? 0;
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
      if (searchMode !== 'metadata' && search.trim()) {
        // Rank by semantic similarity (or lexical+semantic RRF fusion for "both"), then intersect
        // with the active structured filters. Ranked results are capped at 50, so request a full
        // page for the intersection.
        const [ranked, filtered] = await Promise.all([
          searchMode === 'hybrid'
            ? client.search(search.trim(), 'hybrid', 50)
            : client.semanticSearch(search.trim(), 50),
          client.listWorks({ ...structuredQuery(), perPage: 500 }),
        ]);
        semanticDegraded = ranked.degraded === true;
        const byId = new Map(filtered.items.map((w) => [w.id, w]));
        const ordered = ranked.items.map((i) => byId.get(i.work_id)).filter((w): w is Work => !!w);
        // Re-sort the ranked set client-side so the chosen column ordering applies in semantic mode.
        works = sortWorksClientSide(ordered);
        page = 1;
        totalPages = 1;
        totalWorks = works.length;
        if (works.length === 0 && ranked.items.length === 0) {
          message =
            searchMode === 'hybrid'
              ? 'No matches. Embeddings are built on first search; if this stays empty, no ' +
                'papers have indexable text yet.'
              : 'No semantic matches. Embeddings are built on first search; if this stays empty, ' +
                'no papers have indexable text yet.';
        }
      } else {
        semanticDegraded = false;
        const result = await client.listWorks({
          q: search,
          ...structuredQuery(),
          sort: columnPrefs.sort.key,
          order: columnPrefs.sort.order,
          page,
        });
        works = result.items;
        // The server clamps `page` into range; mirror its value so the controls stay consistent.
        page = result.page;
        totalPages = result.pages;
        totalWorks = result.total;
        // Remember the effective page size so in-place row removals (delete) can keep the
        // "{n} papers" counter and page count consistent without a full refetch.
        perPage = result.pages > 0 ? Math.ceil(result.total / result.pages) : result.items.length;
      }
      // Drop selections that fell out of the result set.
      const present = new Set(works.map((w) => w.id));
      selectedIds = selectedIds.filter((id) => present.has(id));
      if (selected) selected = works.find((w) => w.id === selected?.id) ?? selected;
    });
  }

  // Jump to a page (clamped locally; the server re-clamps too) and re-fetch.
  function goToPage(target: number): void {
    const next = Math.min(Math.max(1, Math.trunc(target) || 1), totalPages);
    if (next === page) return;
    page = next;
    void loadWorks();
  }

  // Re-run the query from the first page (used when the filter/sort/search changes, so the user
  // isn't stranded on a now-out-of-range page).
  function reload(): void {
    page = 1;
    void loadWorks();
  }

  // Explicit "refresh data" (issue batch 6 #7): re-fetch the current view + counts without a full
  // page reload — e.g. after the local agent pushes new papers, which don't otherwise appear until
  // a filter/sort change or a browser reload. Keeps the current page/filters (unlike reload()).
  async function refreshData(): Promise<void> {
    await loadWorks();
    if (!message) message = 'Refreshed';
  }

  function resetFilters(): void {
    statusFilter = shelfFilter = rackFilter = tagFilter = pdfFilter = refsFilter = '';
    missing = [];
    search = '';
    reload();
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
      const updated = await client.updateWork(work.id, { reading_status: status });
      works = works.map((w) => (w.id === updated.id ? updated : w));
      if (selected?.id === updated.id) selected = updated;
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
      const failed = await runBatched(ids, (id) => client.deleteWork(id));
      if (selected && ids.includes(selected.id)) selectWork(null);
      selectedIds = [];
      await loadWorks();
      message = failed
        ? `Deleted ${ids.length - failed} paper(s); ${failed} failed.`
        : `Deleted ${ids.length} paper(s)`;
    });
  }

  // A per-work batch action (re-extract / keywords / topics / enrich): run `op` over the selection
  // with bounded concurrency and report how many succeeded vs failed.
  async function runWorkBatch(
    op: (id: string) => Promise<unknown>,
    verb: string,
  ): Promise<void> {
    const ids = [...selectedIds];
    await run(async () => {
      const failed = await runBatched(ids, op);
      const ok = ids.length - failed;
      message = failed
        ? `Queued ${verb} for ${ok} paper(s); ${failed} failed (queue unavailable?).`
        : `Queued ${verb} for ${ok} paper(s) — watch the Jobs tab.`;
    });
  }

  async function batchApplyMetadata(): Promise<void> {
    const ids = [...selectedIds];
    const field = batchMetaField;
    await run(async () => {
      const res = await client.bulkApplyMetadata(ids, field);
      const what = field === 'all' ? 'all fields' : field;
      message =
        `Set ${what} from the best source on ${res.applied} paper(s)` +
        (res.skipped ? `; skipped ${res.skipped} (locked / no candidate).` : '.');
      await loadWorks();
    });
  }

  // Dispatch the selected bulk action + Go (issue 3). Per-work extraction uses the tidy
  // /works/{id}/extract endpoint (queues every attached file) rather than looping files client-side.
  async function batchGo(): Promise<void> {
    switch (batchAction) {
      case 'reextract':
        return runWorkBatch((id) => client.extractWork(id), 're-extraction');
      case 'keywords':
        return runWorkBatch((id) => client.keywordsWork(id), 'keyword extraction');
      case 'topics':
        return runWorkBatch((id) => client.topicWork(id), 'topic extraction');
      case 'enrich':
        return runWorkBatch((id) => client.enrichWork(id), 'enrichment');
      case 'apply-metadata':
        return batchApplyMetadata();
      case 'delete':
        return batchDelete();
      default:
        return;
    }
  }

  async function batchSetStatus(): Promise<void> {
    if (!batchStatus) return;
    const ids = [...selectedIds];
    const status = batchStatus;
    await run(async () => {
      const failed = await runBatched(ids, (id) => client.updateWork(id, { reading_status: status }));
      batchStatus = '';
      await loadWorks();
      message = failed
        ? `Set ${ids.length - failed} paper(s) to “${status}”; ${failed} failed.`
        : `Set ${ids.length} paper(s) to “${status}”`;
    });
  }

  async function batchPutInto(): Promise<void> {
    if (!batchPutIntoShelfId) return;
    const shelfId = batchPutIntoShelfId;
    const ids = [...selectedIds];
    await run(async () => {
      const failed = await runBatched(ids, (id) => client.addWorkToShelf(shelfId, id));
      showBatchPutInto = false;
      batchPutIntoShelfId = '';
      message = failed
        ? `Added ${ids.length - failed} paper(s) to shelf; ${failed} failed.`
        : `Added ${ids.length} paper(s) to shelf`;
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
    totalWorks = Math.max(0, totalWorks - 1);
    if (perPage > 0) totalPages = Math.max(1, Math.ceil(totalWorks / perPage));
    selectWork(null);
    message = 'Paper deleted';
  }

  function onImported(): void {
    // A reference was imported into the library as a new paper — reload the list so it appears.
    void loadWorks();
  }
</script>

<section class="layout" bind:this={layoutEl} style="grid-template-columns: {listColPct}% 6px 1fr;">
  <div class="list-col">
    <div class="card">
      <form class="filters" on:submit|preventDefault={reload}>
        <div class="search-row">
          <input
            bind:value={search}
            placeholder={searchMode !== 'metadata'
              ? 'Describe the topic / keywords…'
              : 'Search… e.g. transformer author:doe year:>=2020 has:pdf tag:ml'}
            aria-label="Search"
          />
          <select bind:value={searchMode} title="Search mode" aria-label="Search mode">
            <option value="metadata">metadata</option>
            <option value="semantic">semantic</option>
            <option value="hybrid">both</option>
          </select>
          <button type="submit" disabled={loading} title="Apply search and filters">Search</button>
        </div>
        {#if semanticDegraded}
          <p class="degraded-hint" role="status">Semantic search is using the built-in baseline (sentence-transformers not configured).</p>
        {/if}
        <div class="filter-row">
          <select bind:value={statusFilter} on:change={reload} aria-label="Reading status"
            title="Filter the list by reading status">
            <option value="">Any status</option>
            {#each ['unread', 'skimmed', 'reading', 'read', 'important', 'revisit'] as s}
              <option value={s}>{s}</option>
            {/each}
          </select>
          <select bind:value={shelfFilter} on:change={reload} aria-label="Shelf"
            title="Filter the list by shelf">
            <option value="">Any shelf</option>
            {#each shelves as shelf (shelf.id)}<option value={shelf.id}>{shelf.name}</option>{/each}
          </select>
          <select bind:value={rackFilter} on:change={reload} aria-label="Rack"
            title="Filter the list by rack">
            <option value="">Any rack</option>
            {#each racks as rack (rack.id)}<option value={rack.id}>{rack.name}</option>{/each}
          </select>
          <select bind:value={tagFilter} on:change={reload} aria-label="Tag"
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
          <button type="button" class="secondary" on:click={resetFilters}
            title="Clear the search box and all filters">Reset</button>
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
              <select bind:value={pdfFilter} on:change={reload}
                title="Filter by whether the paper has an attached PDF">
                <option value="">any</option>
                <option value="yes">has file</option>
                <option value="no">no file</option>
              </select>
            </label>
            <label class="inline">References
              <select bind:value={refsFilter} on:change={reload}
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
                    reload();
                  }}
                >{field}</button>
              {/each}
            </span>
          </div>
        {/if}
      </form>
      <div class="bar">
        <span class="muted"
          >{totalWorks} papers{totalPages > 1 ? ` · page ${page} of ${totalPages}` : ''}{message
            ? ` · ${message}`
            : ''}</span
        >
        <span class="bar-actions">
          <button type="button" class="secondary" on:click={refreshData} disabled={loading}
            title="Reload the papers list and counts (e.g. after the local agent pushes new papers)">Refresh</button>
          <button type="button" class="secondary" on:click={jumpToSelected} disabled={!selected}
            title={selected ? 'Scroll the open paper’s row into view' : 'Open a paper to jump to its row'}>Jump to open</button>
          <button type="button" class="secondary" on:click={() => (showColumns = true)}
            title="Choose which columns show and their order">Columns</button>
          <button type="button" class="secondary" on:click={() => (showNew = true)} disabled={!$canEdit}
            title={$canEdit ? 'Create a paper by hand (you can attach a PDF afterwards)' : INSUFFICIENT_ROLE}>+ New paper</button>
        </span>
      </div>
      {#if selectedIds.length > 0}
        <div class="batch">
          <strong>{selectedIds.length} selected</strong>
          <label class="inline">Action
            <select bind:value={batchAction} disabled={loading || !canModifySelected}
              title={canModifySelected ? 'Choose a bulk action to run on the selected papers' : batchHint}>
              <option value="">…</option>
              <option value="reextract">Re-extract</option>
              <option value="keywords">Extract keywords</option>
              <option value="topics">Extract topics</option>
              <option value="enrich">Enrich (DOI/arXiv)</option>
              <option value="apply-metadata">Set metadata from best source</option>
              <option value="delete">Delete</option>
            </select>
          </label>
          {#if batchAction === 'apply-metadata'}
            <label class="inline">Field
              <select bind:value={batchMetaField} disabled={loading || !canModifySelected}
                title="Which field to set from the best available source (GROBID preferred)">
                <option value="all">all fields</option>
                {#each ['title', 'abstract', 'year', 'venue', 'doi'] as f}
                  <option value={f}>{f}</option>
                {/each}
              </select>
            </label>
          {/if}
          <button type="button" class="secondary" on:click={batchGo}
            disabled={loading || !canModifySelected || !batchAction}
            title={canModifySelected ? 'Run the chosen action on the selected papers' : batchHint}>Go</button>
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

    <div class="card list-scroll" bind:this={listScrollEl}>
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
          {flashWorkId}
          selectable
          {selectedIds}
          {allSelected}
          onToggleSelect={toggleSelect}
          onToggleAll={toggleSelectAll}
        />
      {/if}
    </div>

    {#if totalPages > 1}
      <div class="pagination" role="navigation" aria-label="Library pages">
        <button
          type="button"
          class="secondary"
          on:click={() => goToPage(page - 1)}
          disabled={page <= 1 || loading}
          title="Previous page"
        >‹ Prev</button>
        <label class="inline">Page
          <select
            aria-label="Go to page"
            title="Jump to a page"
            value={page}
            on:change={(e) => goToPage(Number(e.currentTarget.value))}
          >
            {#each Array.from({ length: totalPages }, (_, i) => i + 1) as p}
              <option value={p}>{p}</option>
            {/each}
          </select>
          of {totalPages}
        </label>
        <input
          type="number"
          class="page-input"
          min="1"
          max={totalPages}
          value={page}
          aria-label="Go to page number"
          title="Type a page number and press Enter"
          on:change={(e) => goToPage(Number(e.currentTarget.value))}
        />
        <button
          type="button"
          class="secondary"
          on:click={() => goToPage(page + 1)}
          disabled={page >= totalPages || loading}
          title="Next page"
        >Next ›</button>
      </div>
    {/if}
  </div>

  <!-- svelte-ignore a11y-no-noninteractive-tabindex -- draggable resize handle: WAI-ARIA separator pattern requires it to be focusable so arrow keys can also resize it -->
  <!-- svelte-ignore a11y-no-noninteractive-element-interactions -- same: mouse drag + arrow-key resize on a role="separator" div -->
  <div
    class="splitter"
    role="separator"
    aria-orientation="vertical"
    aria-label="Resize the paper list and paper view columns"
    aria-valuenow={Math.round(listColPct)}
    aria-valuemin={25}
    aria-valuemax={75}
    tabindex="0"
    on:mousedown={startSplitterDrag}
    on:keydown={nudgeSplitter}
  ></div>

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
      <ShelfPicker {client} bind:value={batchPutIntoShelfId} modifiableOnly excludeDefault autofocus />
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
    /* ~5% wider than the page's default 96rem section cap, so the list + paper view columns get
       more room on wide screens; grid-template-columns (list % / splitter / detail) is set inline
       from the draggable-splitter state below. */
    max-width: 100.8rem;
    display: grid;
    gap: 0;
    /* Fill the viewport below the header/hint so each column scrolls on its own. The header wraps
       to a variable number of rows, so subtract its measured height (--app-header-h, published by
       App.svelte) plus the tab-hint + content padding (~3rem); fall back to a fixed guess before
       it is measured. Using the real height keeps the pane's bottom on-screen and stops the tall
       wrapped header from overlapping content. */
    height: calc(100dvh - var(--app-header-h, 4rem) - 3rem);
    min-height: 22rem;
  }

  .list-col {
    display: grid;
    gap: 1rem;
    grid-template-rows: auto minmax(0, 1fr);
    min-height: 0;
    padding-right: 1rem;
  }

  .list-scroll {
    min-height: 0;
    overflow-y: auto;
  }

  .detail-col {
    min-height: 0;
    overflow-y: auto;
    padding-left: 1rem;
  }

  .splitter {
    align-self: stretch;
    background: var(--border-normal);
    border-radius: 3px;
    cursor: col-resize;
    justify-self: center;
    width: 6px;
  }

  .splitter:hover,
  .splitter:focus-visible {
    background: var(--accent-primary);
    outline: none;
  }

  .filters {
    display: grid;
    gap: 0.4rem;
  }

  /* Compact controls for the search/filter/action panel only (not a global button resize) —
     smaller than the app default but still comfortably legible/clickable. */
  .filters :global(button),
  .filters :global(select),
  .filters :global(input),
  .bar-actions :global(button),
  .batch :global(button),
  .batch :global(select) {
    min-height: 1.9rem;
    padding: 0.28rem 0.55rem;
    font-size: 0.85rem;
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
    background: var(--status-warning-bg);
    color: var(--status-warning);
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
    background: var(--surface-sunken);
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    padding: 0.4rem 0.6rem;
  }

  .inline {
    align-items: center;
    color: var(--ink-normal);
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
    color: var(--ink-muted);
    font-size: 0.82rem;
    font-weight: 600;
  }

  .chip {
    background: var(--surface-overlay);
    border: 1px solid var(--border-normal);
    border-radius: 999px;
    color: var(--ink-normal);
    font-size: 0.78rem;
    min-height: 1.9rem;
    padding: 0.15rem 0.6rem;
  }

  .chip.on {
    background: var(--accent-primary);
    border-color: var(--accent-primary);
    color: var(--ink-inverse);
  }

  .bar {
    align-items: center;
    display: flex;
    justify-content: space-between;
    margin-top: 0.45rem;
  }

  .bar-actions {
    display: flex;
    gap: 0.5rem;
  }

  .pagination {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    justify-content: center;
    margin-top: 0.6rem;
  }

  .pagination .page-input {
    width: 5rem;
  }

  .batch {
    align-items: center;
    background: var(--status-success-bg);
    border: 1px solid var(--status-success-border);
    border-radius: 6px;
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.45rem;
    padding: 0.35rem 0.55rem;
  }

  .link {
    background: none;
    border: none;
    color: var(--accent-link);
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
      grid-template-columns: 1fr !important;
      height: auto;
    }

    .splitter {
      display: none;
    }

    .list-col {
      padding-right: 0;
    }

    .detail-col {
      padding-left: 0;
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
