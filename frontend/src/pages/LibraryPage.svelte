<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  import {
    ApiClient,
    type Annotation,
    type AnnotationCreate,
    type CitationContext,
    type DuplicateCandidateAction,
    type DuplicateCandidate,
    type CitationGraphResponse,
    type DuplicateCandidateStatus,
    type DuplicateSplitSegment,
    type ExportFormat,
    type ExportScopeType,
    type FileRecord,
    type GraphNodeMode,
    type Rack,
    type ReadingStatus,
    type SemanticSearchItem,
    type Shelf,
    type Source,
    type Summary,
    type SummaryType,
    type Tag,
    type Topic,
    type Work,
  } from '../api/client';
  import CitationGraph from '../components/CitationGraph.svelte';
  import ExportDialog from '../components/ExportDialog.svelte';
  import PaperTable from '../components/PaperTable.svelte';
  import PdfReader from '../components/PdfReader.svelte';

  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';
  const readingQueueStatuses = new Set(['reading', 'important', 'revisit']);

  let token = '';
  let username = '';
  let password = '';
  let message = '';
  let loading = false;
  let search = '';
  let statusFilter = '';
  let shelfFilter = '';
  let rackFilter = '';
  let tagFilter = '';

  let works: Work[] = [];
  let shelves: Shelf[] = [];
  let racks: Rack[] = [];
  let tags: Tag[] = [];
  let sources: Source[] = [];
  let files: FileRecord[] = [];
  let duplicateCandidates: DuplicateCandidate[] = [];
  let duplicateStatusFilter: DuplicateCandidateStatus | '' = 'open';
  let splitDrafts: Record<string, string> = {};

  let selectedWork: Work | null = null;
  let selectedShelf: Shelf | null = null;
  let selectedRack: Rack | null = null;
  let selectedFile: FileRecord | null = null;
  let readerFile: FileRecord | null = null;
  let readerUrl: string | null = null;
  let shelfWorks: Work[] = [];
  let rackShelves: Shelf[] = [];
  let citationContexts: CitationContext[] = [];
  let annotations: Annotation[] = [];
  let summaries: Summary[] = [];
  let semanticQuery = '';
  let semanticResults: SemanticSearchItem[] = [];
  let topics: Topic[] = [];

  let newWorkTitle = '';
  let newWorkYear = '';
  let newWorkVenue = '';
  let newShelfName = '';
  let newRackName = '';
  let newTagName = '';
  let newSourceName = '';
  let newSourceAlias = '';
  let selectedSourceId = '';
  let bibtexContent = '';
  let selectedShelfForWork = '';
  let selectedRackForShelf = '';
  let selectedTagId = '';
  let tagTargetType: 'work' | 'shelf' | 'rack' = 'work';

  $: client = new ApiClient(apiBaseUrl, token || null);
  $: readingQueue = works.filter((work) => readingQueueStatuses.has(work.reading_status));

  onMount(() => {
    token = window.localStorage.getItem('paracord_token') ?? '';
    if (token) refreshAll();
  });

  onDestroy(clearReaderUrl);

  async function run(action: () => Promise<void>, success?: string): Promise<void> {
    loading = true;
    message = '';
    try {
      await action();
      if (success) message = success;
    } catch (error) {
      message = error instanceof Error ? error.message : 'Request failed';
    } finally {
      loading = false;
    }
  }

  async function login(): Promise<void> {
    await run(async () => {
      token = await new ApiClient(apiBaseUrl).login(username, password);
      window.localStorage.setItem('paracord_token', token);
      password = '';
      await refreshAll();
    }, 'Signed in');
  }

  function logout(): void {
    token = '';
    window.localStorage.removeItem('paracord_token');
    works = [];
    shelves = [];
    racks = [];
    tags = [];
    sources = [];
    files = [];
    duplicateCandidates = [];
    selectedWork = null;
    selectedShelf = null;
    selectedRack = null;
    selectedFile = null;
    readerFile = null;
    clearReaderUrl();
    shelfWorks = [];
    rackShelves = [];
    citationContexts = [];
    annotations = [];
  }

  async function refreshAll(): Promise<void> {
    await run(async () => {
      await Promise.all([
        loadWorks(),
        loadShelves(),
        loadRacks(),
        loadTags(),
        loadSources(),
        loadFiles(),
        loadDuplicateCandidates(),
      ]);
    });
  }

  async function loadWorks(): Promise<void> {
    works = await client.listWorks({
      q: search,
      readingStatus: statusFilter,
      shelfId: shelfFilter,
      rackId: rackFilter,
      tagId: tagFilter,
    });
    if (selectedWork) selectedWork = works.find((work) => work.id === selectedWork?.id) ?? null;
    if (!selectedWork) {
      citationContexts = [];
      annotations = [];
    }
  }

  async function loadShelves(): Promise<void> {
    shelves = await client.listShelves();
    if (selectedShelf) selectedShelf = shelves.find((shelf) => shelf.id === selectedShelf?.id) ?? null;
  }

  async function loadRacks(): Promise<void> {
    racks = await client.listRacks();
    if (selectedRack) selectedRack = racks.find((rack) => rack.id === selectedRack?.id) ?? null;
  }

  async function loadTags(): Promise<void> {
    tags = await client.listTags();
  }

  async function loadSources(): Promise<void> {
    sources = await client.listSources();
    if (!selectedSourceId && sources[0]) selectedSourceId = sources[0].id;
  }

  async function loadFiles(): Promise<void> {
    files = await client.listFiles();
    if (!selectedFile && files[0]) selectedFile = files[0];
  }

  async function loadDuplicateCandidates(): Promise<void> {
    duplicateCandidates = await client.listDuplicateCandidates(duplicateStatusFilter);
  }

  async function searchWorks(): Promise<void> {
    await run(loadWorks);
  }

  async function createWork(): Promise<void> {
    await run(async () => {
      const work = await client.createWork({
        canonical_title: newWorkTitle,
        venue: newWorkVenue || null,
        year: newWorkYear ? Number(newWorkYear) : null,
      });
      newWorkTitle = '';
      newWorkYear = '';
      newWorkVenue = '';
      await loadWorks();
      selectedWork = work;
    }, 'Work created');
  }

  async function updateStatus(work: Work, readingStatus: ReadingStatus): Promise<void> {
    await run(async () => {
      await client.updateWork(work.id, { reading_status: readingStatus });
      await loadWorks();
      if (selectedShelf) await selectShelf(selectedShelf);
    });
  }

  async function selectWork(work: Work): Promise<void> {
    selectedWork = work;
    [citationContexts, annotations, summaries] = await Promise.all([
      client.listCitationContexts(work.id),
      client.listAnnotations(work.id),
      client.listSummaries(work.id),
    ]);
  }

  async function selectWorkById(workId: string): Promise<void> {
    const known = works.find((work) => work.id === workId);
    if (known) {
      await selectWork(known);
      return;
    }
    await run(async () => {
      await selectWork(await client.getWork(workId));
    });
  }

  async function runTopicModel(): Promise<void> {
    const scope = selectedShelf
      ? { scopeType: 'shelf' as const, scopeId: selectedShelf.id }
      : selectedRack
        ? { scopeType: 'rack' as const, scopeId: selectedRack.id }
        : { scopeType: 'library' as const, scopeId: null };
    await run(async () => {
      const response = await client.modelTopics({ ...scope, maxTopics: 6 });
      topics = response.topics;
      message = `Topics: ${response.topics.length} over ${response.work_count} works`;
    });
  }

  async function runSemanticSearch(): Promise<void> {
    if (!semanticQuery.trim()) return;
    await run(async () => {
      const response = await client.semanticSearch(semanticQuery, 10);
      semanticResults = response.items;
    }, 'Semantic search complete');
  }

  async function summarizeSelectedWork(summaryType: SummaryType): Promise<void> {
    if (!selectedWork) return;
    const work = selectedWork;
    await run(async () => {
      await client.createSummary(work.id, summaryType);
      summaries = await client.listSummaries(work.id);
    }, 'Summary generated');
  }

  async function createShelf(): Promise<void> {
    await run(async () => {
      const shelf = await client.createShelf({ name: newShelfName });
      newShelfName = '';
      await loadShelves();
      await selectShelf(shelf);
    }, 'Shelf created');
  }

  async function createRack(): Promise<void> {
    await run(async () => {
      const rack = await client.createRack({ name: newRackName });
      newRackName = '';
      await loadRacks();
      await selectRack(rack);
    }, 'Rack created');
  }

  async function createTag(): Promise<void> {
    await run(async () => {
      const tag = await client.createTag({ name: newTagName });
      newTagName = '';
      await loadTags();
      selectedTagId = tag.id;
    }, 'Tag created');
  }

  async function createSource(): Promise<void> {
    await run(async () => {
      const source = await client.createServerFolderSource({
        name: newSourceName,
        path_alias: newSourceAlias,
      });
      newSourceName = '';
      newSourceAlias = '';
      await loadSources();
      selectedSourceId = source.id;
    }, 'Source created');
  }

  async function importSource(): Promise<void> {
    if (!selectedSourceId) return;
    await run(async () => {
      const batch = await client.importFolder(selectedSourceId);
      await Promise.all([loadWorks(), loadFiles()]);
      message = `Import ${batch.status}: ${batch.stats?.seen ?? 0} PDFs scanned`;
    });
  }

  async function importBibtex(): Promise<void> {
    if (!bibtexContent.trim()) return;
    await run(async () => {
      const batch = await client.importBibtex(bibtexContent);
      bibtexContent = '';
      await loadWorks();
      message = `BibTeX import: ${batch.stats?.created ?? 0} created, ${batch.stats?.matched ?? 0} matched`;
    });
  }

  async function addSelectedWorkToShelf(): Promise<void> {
    if (!selectedWork || !selectedShelfForWork) return;
    await run(async () => {
      await client.addWorkToShelf(selectedShelfForWork, selectedWork.id);
      const shelf = shelves.find((item) => item.id === selectedShelfForWork);
      if (shelf) await selectShelf(shelf);
    }, 'Work added');
  }

  async function addSelectedShelfToRack(): Promise<void> {
    if (!selectedShelf || !selectedRackForShelf) return;
    await run(async () => {
      await client.addShelfToRack(selectedRackForShelf, selectedShelf.id);
      const rack = racks.find((item) => item.id === selectedRackForShelf);
      if (rack) await selectRack(rack);
    }, 'Shelf added');
  }

  async function tagSelectedEntity(): Promise<void> {
    if (!selectedTagId) return;
    const entityId =
      tagTargetType === 'work'
        ? selectedWork?.id
        : tagTargetType === 'shelf'
          ? selectedShelf?.id
          : selectedRack?.id;
    if (!entityId) return;
    await run(async () => {
      await client.addTagLink(selectedTagId, tagTargetType, entityId);
    }, 'Tag added');
  }

  async function removeSelectedTagLink(): Promise<void> {
    if (!selectedTagId) return;
    const entityId =
      tagTargetType === 'work'
        ? selectedWork?.id
        : tagTargetType === 'shelf'
          ? selectedShelf?.id
          : selectedRack?.id;
    if (!entityId) return;
    await run(async () => {
      await client.removeTagLink(selectedTagId, tagTargetType, entityId);
    }, 'Tag removed');
  }

  async function archiveSelectedShelf(): Promise<void> {
    if (!selectedShelf) return;
    await run(async () => {
      await client.updateShelf(selectedShelf.id, { status: 'archived' });
      selectedShelf = null;
      shelfWorks = [];
      await loadShelves();
    }, 'Shelf archived');
  }

  async function archiveSelectedRack(): Promise<void> {
    if (!selectedRack) return;
    await run(async () => {
      await client.updateRack(selectedRack.id, { status: 'archived' });
      selectedRack = null;
      rackShelves = [];
      await loadRacks();
    }, 'Rack archived');
  }

  async function removeSelectedWorkFromShelf(): Promise<void> {
    if (!selectedShelf || !selectedWork) return;
    await run(async () => {
      await client.removeWorkFromShelf(selectedShelf.id, selectedWork.id);
      await selectShelf(selectedShelf as Shelf);
    }, 'Work removed');
  }

  async function removeSelectedShelfFromRack(): Promise<void> {
    if (!selectedRack || !selectedShelf) return;
    await run(async () => {
      await client.removeShelfFromRack(selectedRack.id, selectedShelf.id);
      await selectRack(selectedRack as Rack);
    }, 'Shelf removed');
  }

  async function openSelectedFile(): Promise<void> {
    if (!selectedFile) return;
    await run(async () => {
      const blob = await client.getFileBlob(selectedFile.id);
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener,noreferrer');
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    });
  }

  async function exportScope(
    scopeType: ExportScopeType,
    scopeId: string,
    format: ExportFormat,
  ): Promise<void> {
    await run(async () => {
      const result = await client.exportCitations({
        scope_type: scopeType,
        scope_id: scopeId,
        format,
      });
      const url = URL.createObjectURL(new Blob([result.content], { type: result.content_type }));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = result.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    }, 'Export downloaded');
  }

  async function loadCitationGraph(nodeMode: GraphNodeMode): Promise<CitationGraphResponse> {
    // Scope to the selected shelf/rack when one is chosen, otherwise the whole library.
    const scope = selectedShelf
      ? { scopeType: 'shelf' as const, scopeId: selectedShelf.id }
      : selectedRack
        ? { scopeType: 'rack' as const, scopeId: selectedRack.id }
        : { scopeType: 'library' as const, scopeId: null };
    return client.citationGraph({ ...scope, nodeMode });
  }

  async function openSelectedFileInReader(): Promise<void> {
    if (!selectedFile) return;
    await run(async () => {
      const blob = await client.getFileBlob(selectedFile.id);
      clearReaderUrl();
      readerUrl = URL.createObjectURL(blob);
      readerFile = selectedFile;
    });
  }

  function clearReaderUrl(): void {
    if (readerUrl) URL.revokeObjectURL(readerUrl);
    readerUrl = null;
  }

  async function createReaderAnnotation(payload: AnnotationCreate): Promise<void> {
    if (!selectedWork) throw new Error('Select a work before adding annotations');
    await run(async () => {
      await client.createAnnotation(selectedWork.id, {
        ...payload,
        file_id: readerFile?.id ?? selectedFile?.id ?? null,
      });
      annotations = await client.listAnnotations(selectedWork.id);
    }, 'Annotation added');
  }

  async function scanDuplicateCandidates(): Promise<void> {
    await run(async () => {
      const result = await client.scanDuplicateCandidates();
      await loadDuplicateCandidates();
      message = `Duplicate scan: ${result.candidate_count} candidates across ${result.scanned_works} works and ${result.scanned_files} files`;
    });
  }

  async function updateDuplicateStatus(
    candidate: DuplicateCandidate,
    status: DuplicateCandidateStatus,
  ): Promise<void> {
    await run(async () => {
      await client.updateDuplicateCandidate(candidate.id, status);
      await loadDuplicateCandidates();
    }, `Candidate marked ${status}`);
  }

  async function applyDuplicateAction(
    candidate: DuplicateCandidate,
    action: DuplicateCandidateAction,
  ): Promise<void> {
    await run(async () => {
      await client.applyDuplicateCandidateAction(candidate.id, action, {
        targetWorkId: canResolveAsWork(candidate)
          ? (candidate.suggested_target_work_id ?? candidate.entity_a_id)
          : undefined,
      });
      await Promise.all([loadWorks(), loadShelves(), loadRacks(), loadDuplicateCandidates()]);
    }, `Applied ${candidateLabel(action)}`);
  }

  async function splitMultiworkCandidate(candidate: DuplicateCandidate): Promise<void> {
    await run(async () => {
      const splitSegments = parseSplitDraft(splitDrafts[candidate.id] ?? '');
      await client.applyDuplicateCandidateAction(candidate.id, 'split_file', { splitSegments });
      splitDrafts = { ...splitDrafts, [candidate.id]: '' };
      await Promise.all([loadWorks(), loadFiles(), loadDuplicateCandidates()]);
    }, 'File split recorded');
  }

  function setSplitDraft(candidateId: string, value: string): void {
    splitDrafts = { ...splitDrafts, [candidateId]: value };
  }

  async function selectShelf(shelf: Shelf): Promise<void> {
    selectedShelf = shelf;
    selectedShelfForWork = shelf.id;
    shelfWorks = await client.listShelfWorks(shelf.id);
  }

  async function selectRack(rack: Rack): Promise<void> {
    selectedRack = rack;
    selectedRackForShelf = rack.id;
    rackShelves = await client.listRackShelves(rack.id);
  }

  function formatBytes(value: number): string {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  function candidateLabel(value: string): string {
    return value.replaceAll('_', ' ');
  }

  function candidateEntities(candidate: DuplicateCandidate): string {
    const a =
      candidate.entity_a_label ??
      `${candidate.entity_a_type}:${candidate.entity_a_id.slice(0, 8)}`;
    const b =
      candidate.entity_b_label ??
      `${candidate.entity_b_type}:${candidate.entity_b_id.slice(0, 8)}`;
    return a === b ? a : `${a} ↔ ${b}`;
  }

  function candidateSignals(candidate: DuplicateCandidate): string {
    const entries = Object.entries(candidate.signals ?? {});
    if (!entries.length) return 'No signals';
    return entries
      .slice(0, 4)
      .map(([key, value]) => `${candidateLabel(key)}: ${String(value)}`)
      .join(' · ');
  }

  function canResolveAsWork(candidate: DuplicateCandidate): boolean {
    return candidate.entity_a_type === 'work' && candidate.entity_b_type === 'work';
  }

  function canResolveAsFile(candidate: DuplicateCandidate): boolean {
    return (
      candidate.candidate_type !== 'multiwork_file' &&
      candidate.entity_a_type === 'file' &&
      candidate.entity_b_type === 'file'
    );
  }

  function canSplitMultiwork(candidate: DuplicateCandidate): boolean {
    return candidate.candidate_type === 'multiwork_file';
  }

  function parseSplitDraft(value: string): DuplicateSplitSegment[] {
    const segments = value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [title, start, end] = line.split('|').map((part) => part.trim());
        return {
          title,
          page_start: start ? Number(start) : undefined,
          page_end: end ? Number(end) : undefined,
        };
      })
      .filter((segment) => segment.title);
    if (!segments.length) throw new Error('Enter at least one segment as Title | start | end');
    return segments;
  }
</script>

{#if !token}
  <section class="login-panel">
    <form on:submit|preventDefault={login}>
      <label>
        Username
        <input bind:value={username} autocomplete="username" />
      </label>
      <label>
        Password
        <input bind:value={password} type="password" autocomplete="current-password" />
      </label>
      <button type="submit" disabled={loading || !username || !password}>Sign in</button>
    </form>
    {#if message}<p class="message">{message}</p>{/if}
  </section>
{:else}
  <section class="toolbar">
    <form on:submit|preventDefault={searchWorks}>
      <input bind:value={search} placeholder="Search title, DOI, arXiv, venue" />
      <select bind:value={statusFilter}>
        <option value="">All statuses</option>
        <option value="unread">unread</option>
        <option value="skimmed">skimmed</option>
        <option value="reading">reading</option>
        <option value="read">read</option>
        <option value="important">important</option>
        <option value="revisit">revisit</option>
      </select>
      <select bind:value={shelfFilter}>
        <option value="">Any shelf</option>
        {#each shelves as shelf}
          <option value={shelf.id}>{shelf.name}</option>
        {/each}
      </select>
      <select bind:value={rackFilter}>
        <option value="">Any rack</option>
        {#each racks as rack}
          <option value={rack.id}>{rack.name}</option>
        {/each}
      </select>
      <select bind:value={tagFilter}>
        <option value="">Any tag</option>
        {#each tags as tag}
          <option value={tag.id}>{tag.name}</option>
        {/each}
      </select>
      <button type="submit" disabled={loading}>Search</button>
    </form>
    <button type="button" on:click={refreshAll} disabled={loading}>Refresh</button>
    <button type="button" on:click={logout}>Sign out</button>
  </section>

  {#if message}<p class="message">{message}</p>{/if}

  <section class="workspace">
    <div class="main-column">
      <section class="surface">
        <div class="section-head">
          <h2>Library</h2>
          <span>{works.length}</span>
        </div>
        <PaperTable
          {works}
          selectedWorkId={selectedWork?.id ?? null}
          onSelect={selectWork}
          onStatusChange={updateStatus}
        />
      </section>

      <section class="surface">
        <div class="section-head">
          <h2>Citation Contexts</h2>
          <span>{citationContexts.length}</span>
        </div>
        {#if !selectedWork}
          <p class="empty">Select a work</p>
        {:else if citationContexts.length === 0}
          <p class="empty">No citation contexts extracted</p>
        {:else}
          <div class="context-list">
            {#each citationContexts as context}
              <article>
                <header>
                  <strong>{context.marker_text ?? 'citation'}</strong>
                  <span>{context.section_label ?? 'section unknown'}</span>
                </header>
                <p>{context.context_sentence ?? 'No sentence context'}</p>
                <small>
                  {context.reference_title ?? context.reference_raw_citation ?? 'Unparsed reference'}
                </small>
              </article>
            {/each}
          </div>
        {/if}
      </section>

      <section class="surface">
        <div class="section-head">
          <h2>Semantic Search</h2>
          <span>{semanticResults.length}</span>
        </div>
        <form on:submit|preventDefault={runSemanticSearch} class="inline-action">
          <input bind:value={semanticQuery} placeholder="Search by meaning…" />
          <button type="submit" disabled={!semanticQuery.trim() || loading}>Search</button>
        </form>
        {#if semanticResults.length === 0}
          <p class="empty">No semantic results yet</p>
        {:else}
          <ul class="plain-list">
            {#each semanticResults as hit (hit.work_id)}
              <li>
                <button type="button" on:click={() => selectWorkById(hit.work_id)}>
                  {hit.title ?? 'Untitled work'}
                </button>
                <small>{(hit.score * 100).toFixed(0)}%{hit.year ? ` · ${hit.year}` : ''}</small>
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      <section class="surface">
        <div class="section-head">
          <h2>Summaries</h2>
          <span>{summaries.length}</span>
        </div>
        {#if !selectedWork}
          <p class="empty">Select a work</p>
        {:else}
          <div class="inline-action">
            <button
              type="button"
              on:click={() => summarizeSelectedWork('abstract')}
              disabled={loading}
            >
              Abstract
            </button>
            <button
              type="button"
              on:click={() => summarizeSelectedWork('extractive')}
              disabled={loading}
            >
              Extractive
            </button>
          </div>
          {#if summaries.length === 0}
            <p class="empty">No summaries yet</p>
          {:else}
            <div class="context-list">
              {#each summaries as summary}
                <article>
                  <header>
                    <strong>{summary.summary_type}</strong>
                    <span>{summary.model_name ?? ''}</span>
                  </header>
                  <p>{summary.text}</p>
                </article>
              {/each}
            </div>
          {/if}
        {/if}
      </section>

      <section class="surface">
        <div class="section-head">
          <h2>Duplicate Review</h2>
          <span>{duplicateCandidates.length}</span>
        </div>
        <div class="inline-action review-actions">
          <select bind:value={duplicateStatusFilter} on:change={loadDuplicateCandidates}>
            <option value="open">Open</option>
            <option value="accepted">Accepted</option>
            <option value="rejected">Rejected</option>
            <option value="ignored">Ignored</option>
            <option value="">All</option>
          </select>
          <button type="button" on:click={scanDuplicateCandidates} disabled={loading}>
            Scan
          </button>
        </div>
        {#if duplicateCandidates.length === 0}
          <p class="empty">No duplicate candidates</p>
        {:else}
          <div class="duplicate-list">
            {#each duplicateCandidates as candidate}
              <article>
                <header>
                  <div>
                    <strong>{candidateLabel(candidate.candidate_type)}</strong>
                    <span>{candidateEntities(candidate)}</span>
                  </div>
                  <b>{Math.round(candidate.score * 100)}%</b>
                </header>
                <p>{candidateSignals(candidate)}</p>
                <div class="candidate-actions">
                  {#if canResolveAsWork(candidate)}
                    <button
                      type="button"
                      on:click={() => applyDuplicateAction(candidate, 'merge_works')}
                      disabled={loading || candidate.status !== 'open'}
                    >
                      Merge
                    </button>
                    <button
                      type="button"
                      on:click={() => applyDuplicateAction(candidate, 'link_as_version')}
                      disabled={loading || candidate.status !== 'open'}
                    >
                      Link version
                    </button>
                  {/if}
                  {#if canResolveAsFile(candidate)}
                    <button
                      type="button"
                      on:click={() => applyDuplicateAction(candidate, 'mark_duplicate_file')}
                      disabled={loading || candidate.status !== 'open'}
                    >
                      Mark duplicate
                    </button>
                  {/if}
                  {#if canSplitMultiwork(candidate)}
                    <textarea
                      value={splitDrafts[candidate.id] ?? ''}
                      on:input={(event) =>
                        setSplitDraft(candidate.id, event.currentTarget.value)}
                      placeholder="Title | start page | end page"
                      disabled={loading || candidate.status !== 'open'}
                    ></textarea>
                    <button
                      type="button"
                      on:click={() => splitMultiworkCandidate(candidate)}
                      disabled={loading || candidate.status !== 'open'}
                    >
                      Split file
                    </button>
                  {/if}
                  <button
                    type="button"
                    on:click={() => applyDuplicateAction(candidate, 'keep_separate')}
                    disabled={loading || candidate.status !== 'open'}
                  >
                    Keep separate
                  </button>
                  <button
                    type="button"
                    on:click={() => applyDuplicateAction(candidate, 'ignore')}
                    disabled={loading || candidate.status !== 'open'}
                  >
                    Ignore
                  </button>
                  {#if candidate.status !== 'open'}
                    <button
                      type="button"
                      on:click={() => updateDuplicateStatus(candidate, 'open')}
                      disabled={loading}
                    >
                      Reopen
                    </button>
                  {/if}
                </div>
              </article>
            {/each}
          </div>
        {/if}
      </section>

      <section class="surface">
        <CitationGraph
          label={selectedShelf
            ? `· shelf "${selectedShelf.name}"`
            : selectedRack
              ? `· rack "${selectedRack.name}"`
              : '· whole library'}
          disabled={loading}
          load={loadCitationGraph}
        />
      </section>

      <section class="surface">
        <div class="section-head">
          <h2>Topics</h2>
          <span>{topics.length}</span>
        </div>
        <div class="inline-action">
          <button type="button" on:click={runTopicModel} disabled={loading}>
            Model topics ({selectedShelf
              ? `shelf "${selectedShelf.name}"`
              : selectedRack
                ? `rack "${selectedRack.name}"`
                : 'library'})
          </button>
        </div>
        {#if topics.length === 0}
          <p class="empty">No topics modeled yet</p>
        {:else}
          <ul class="plain-list">
            {#each topics as topic (topic.topic_id)}
              <li>
                <strong>{topic.keywords.join(', ')}</strong>
                <small>{topic.work_count} works</small>
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      <section class="surface">
        <div class="section-head">
          <h2>Reading Queue</h2>
          <span>{readingQueue.length}</span>
        </div>
        <PaperTable
          works={readingQueue}
          selectedWorkId={selectedWork?.id ?? null}
          compact
          onSelect={selectWork}
          onStatusChange={updateStatus}
        />
      </section>

      <section class="surface">
        <div class="section-head">
          <h2>Files</h2>
          <span>{files.length}</span>
        </div>
        <div class="files-grid">
          <div class="file-list">
            {#each files as file}
              <button
                type="button"
                class:selected={file.id === selectedFile?.id}
                on:click={() => (selectedFile = file)}
              >
                <strong>{file.original_filename ?? file.id}</strong>
                <span>{formatBytes(file.size_bytes)} · {file.text_layer_quality}</span>
              </button>
            {/each}
          </div>
          <article class="file-preview">
            {#if selectedFile}
              <h3>{selectedFile.original_filename ?? selectedFile.id}</h3>
              <dl>
                <div><dt>Pages</dt><dd>{selectedFile.page_count ?? '-'}</dd></div>
                <div><dt>Status</dt><dd>{selectedFile.status}</dd></div>
                <div><dt>SHA-256</dt><dd>{selectedFile.sha256}</dd></div>
              </dl>
              <button type="button" on:click={openSelectedFile} disabled={loading}>Open PDF</button>
              <button type="button" on:click={openSelectedFileInReader} disabled={loading}>
                Read
              </button>
              <pre>{selectedFile.preview_text ?? 'No preview text'}</pre>
            {:else}
              <p class="empty">No files</p>
            {/if}
          </article>
        </div>
      </section>

      <section class="surface">
        <PdfReader
          fileId={readerFile?.id ?? selectedFile?.id ?? ''}
          fileName={readerFile?.original_filename ?? selectedFile?.original_filename ?? 'PDF reader'}
          fileUrl={readerUrl}
          contexts={citationContexts}
          {annotations}
          onCreateAnnotation={selectedWork ? createReaderAnnotation : null}
        />
      </section>
    </div>

    <aside class="side-column">
      <section class="surface">
        <h2>New Work</h2>
        <form on:submit|preventDefault={createWork} class="stack">
          <input bind:value={newWorkTitle} placeholder="Title" />
          <div class="split">
            <input bind:value={newWorkYear} placeholder="Year" inputmode="numeric" />
            <input bind:value={newWorkVenue} placeholder="Venue" />
          </div>
          <button type="submit" disabled={!newWorkTitle || loading}>Create</button>
        </form>
      </section>

      <section class="surface">
        <h2>Sources</h2>
        <form on:submit|preventDefault={createSource} class="stack">
          <input bind:value={newSourceName} placeholder="Name" />
          <input bind:value={newSourceAlias} placeholder="Configured alias" />
          <button type="submit" disabled={!newSourceName || !newSourceAlias || loading}>Add</button>
        </form>
        <div class="inline-action">
          <select bind:value={selectedSourceId}>
            <option value="">Source</option>
            {#each sources as source}
              <option value={source.id}>{source.name}</option>
            {/each}
          </select>
          <button type="button" on:click={importSource} disabled={!selectedSourceId || loading}>
            Import
          </button>
        </div>
        <form on:submit|preventDefault={importBibtex} class="stack">
          <textarea
            bind:value={bibtexContent}
            placeholder="Paste BibTeX entries…"
            rows="4"
          ></textarea>
          <button type="submit" disabled={!bibtexContent.trim() || loading}>Import BibTeX</button>
        </form>
      </section>

      <section class="surface">
        <div class="section-head">
          <h2>Shelves</h2>
          <span>{shelves.length}</span>
        </div>
        <form on:submit|preventDefault={createShelf} class="inline-action">
          <input bind:value={newShelfName} placeholder="Shelf name" />
          <button type="submit" disabled={!newShelfName || loading}>Add</button>
        </form>
        <div class="chip-list">
          {#each shelves as shelf}
            <button
              type="button"
              class:selected={shelf.id === selectedShelf?.id}
              on:click={() => selectShelf(shelf)}
            >
              {shelf.name}
            </button>
          {/each}
        </div>
        <div class="inline-action">
          <button type="button" on:click={archiveSelectedShelf} disabled={!selectedShelf || loading}>
            Archive shelf
          </button>
          <button
            type="button"
            on:click={removeSelectedWorkFromShelf}
            disabled={!selectedShelf || !selectedWork || loading}
          >
            Remove work
          </button>
        </div>
        {#if selectedShelf}
          <ExportDialog
            label={`shelf "${selectedShelf.name}"`}
            disabled={loading}
            onExport={(format) => exportScope('shelf', selectedShelf!.id, format)}
          />
        {/if}
        <div class="inline-action">
          <select bind:value={selectedShelfForWork}>
            <option value="">Shelf</option>
            {#each shelves as shelf}
              <option value={shelf.id}>{shelf.name}</option>
            {/each}
          </select>
          <button
            type="button"
            on:click={addSelectedWorkToShelf}
            disabled={!selectedWork || !selectedShelfForWork || loading}
          >
            Add work
          </button>
        </div>
        <PaperTable
          works={shelfWorks}
          compact
          selectedWorkId={selectedWork?.id ?? null}
          onSelect={selectWork}
          onStatusChange={updateStatus}
        />
      </section>

      <section class="surface">
        <div class="section-head">
          <h2>Racks</h2>
          <span>{racks.length}</span>
        </div>
        <form on:submit|preventDefault={createRack} class="inline-action">
          <input bind:value={newRackName} placeholder="Rack name" />
          <button type="submit" disabled={!newRackName || loading}>Add</button>
        </form>
        <div class="chip-list">
          {#each racks as rack}
            <button
              type="button"
              class:selected={rack.id === selectedRack?.id}
              on:click={() => selectRack(rack)}
            >
              {rack.name}
            </button>
          {/each}
        </div>
        <div class="inline-action">
          <select bind:value={selectedRackForShelf}>
            <option value="">Rack</option>
            {#each racks as rack}
              <option value={rack.id}>{rack.name}</option>
            {/each}
          </select>
          <button
            type="button"
            on:click={addSelectedShelfToRack}
            disabled={!selectedShelf || !selectedRackForShelf || loading}
          >
            Add shelf
          </button>
        </div>
        <ul class="plain-list">
          {#each rackShelves as shelf}
            <li>
              <button type="button" on:click={() => (selectedShelf = shelf)}>{shelf.name}</button>
            </li>
          {/each}
        </ul>
        <div class="inline-action">
          <button type="button" on:click={archiveSelectedRack} disabled={!selectedRack || loading}>
            Archive rack
          </button>
          <button
            type="button"
            on:click={removeSelectedShelfFromRack}
            disabled={!selectedRack || !selectedShelf || loading}
          >
            Remove shelf
          </button>
        </div>
        {#if selectedRack}
          <ExportDialog
            label={`rack "${selectedRack.name}"`}
            disabled={loading}
            onExport={(format) => exportScope('rack', selectedRack!.id, format)}
          />
        {/if}
      </section>

      <section class="surface">
        <div class="section-head">
          <h2>Tags</h2>
          <span>{tags.length}</span>
        </div>
        <form on:submit|preventDefault={createTag} class="inline-action">
          <input bind:value={newTagName} placeholder="Tag name" />
          <button type="submit" disabled={!newTagName || loading}>Add</button>
        </form>
        <div class="inline-action">
          <select bind:value={selectedTagId}>
            <option value="">Tag</option>
            {#each tags as tag}
              <option value={tag.id}>{tag.name}</option>
            {/each}
          </select>
          <select bind:value={tagTargetType}>
            <option value="work">Work</option>
            <option value="shelf">Shelf</option>
            <option value="rack">Rack</option>
          </select>
          <button type="button" on:click={tagSelectedEntity} disabled={!selectedTagId || loading}>
            Apply
          </button>
        </div>
        <button type="button" on:click={removeSelectedTagLink} disabled={!selectedTagId || loading}>
          Remove from target
        </button>
      </section>
    </aside>
  </section>
{/if}

<style>
  .login-panel,
  .toolbar,
  .surface {
    background: #fbfcfd;
    border: 1px solid #d8dee6;
    border-radius: 8px;
  }

  .login-panel {
    margin: 5rem auto 0;
    max-width: 27rem;
    padding: 1.2rem;
  }

  .toolbar {
    align-items: center;
    display: flex;
    gap: 0.6rem;
    justify-content: space-between;
    margin-bottom: 1rem;
    padding: 0.7rem;
  }

  .toolbar form {
    display: grid;
    flex: 1;
    gap: 0.5rem;
    grid-template-columns: minmax(12rem, 1fr) repeat(4, minmax(8rem, 10rem)) auto;
  }

  .workspace {
    align-items: start;
    display: grid;
    gap: 1rem;
    grid-template-columns: minmax(0, 1fr) minmax(22rem, 28rem);
  }

  .main-column,
  .side-column {
    display: grid;
    gap: 1rem;
  }

  .surface {
    overflow: hidden;
    padding: 0.85rem;
  }

  .section-head {
    align-items: center;
    display: flex;
    justify-content: space-between;
    margin-bottom: 0.65rem;
  }

  h2,
  h3 {
    color: #1f2a36;
    font-size: 1rem;
    line-height: 1.2;
    margin: 0;
  }

  h3 {
    overflow-wrap: anywhere;
  }

  .section-head span,
  .message {
    color: #667381;
    font-size: 0.86rem;
  }

  .message {
    margin: 0.4rem 0 1rem;
  }

  form,
  .stack,
  .inline-action,
  .split {
    display: grid;
    gap: 0.5rem;
  }

  .inline-action,
  .split {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  label {
    color: #526070;
    display: grid;
    font-size: 0.8rem;
    gap: 0.25rem;
  }

  input,
  select,
  textarea,
  button {
    border: 1px solid #bcc7d2;
    border-radius: 6px;
    font: inherit;
    min-height: 2.35rem;
    padding: 0.45rem 0.55rem;
  }

  input,
  select,
  textarea {
    background: white;
    min-width: 0;
  }

  textarea {
    min-height: 4.8rem;
    resize: vertical;
  }

  button {
    background: #203142;
    color: white;
    cursor: pointer;
    font-weight: 700;
  }

  button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  .chip-list {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin: 0.65rem 0;
  }

  .chip-list button,
  .file-list button {
    background: white;
    color: #21303d;
    font-weight: 600;
  }

  .chip-list button.selected,
  .file-list button.selected {
    background: #dfece3;
    border-color: #8eb39a;
  }

  .context-list {
    display: grid;
    gap: 0.65rem;
    max-height: 18rem;
    overflow: auto;
  }

  .duplicate-list {
    display: grid;
    gap: 0.65rem;
    max-height: 22rem;
    overflow: auto;
  }

  .duplicate-list article {
    background: #eef2f6;
    border: 1px solid #d8dee6;
    border-radius: 6px;
    padding: 0.7rem;
  }

  .duplicate-list header,
  .candidate-actions {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
  }

  .duplicate-list header div {
    display: grid;
    gap: 0.2rem;
    min-width: 0;
  }

  .duplicate-list header span,
  .duplicate-list p {
    color: #667381;
    overflow-wrap: anywhere;
  }

  .duplicate-list p {
    font-size: 0.86rem;
    margin: 0.45rem 0;
  }

  .candidate-actions {
    justify-content: start;
    flex-wrap: wrap;
  }

  .candidate-actions button {
    min-height: 2rem;
    padding: 0.3rem 0.5rem;
  }

  .candidate-actions textarea {
    flex: 1 1 16rem;
    width: 100%;
  }

  .review-actions {
    grid-template-columns: minmax(8rem, 12rem) auto;
    margin-bottom: 0.65rem;
  }

  .context-list article {
    background: #eef2f6;
    border: 1px solid #d8dee6;
    border-radius: 6px;
    padding: 0.7rem;
  }

  .context-list header {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
    margin-bottom: 0.35rem;
  }

  .context-list p,
  .context-list small {
    overflow-wrap: anywhere;
  }

  .context-list p {
    margin: 0 0 0.35rem;
  }

  .context-list small {
    color: #667381;
  }

  .files-grid {
    display: grid;
    gap: 0.9rem;
    grid-template-columns: minmax(12rem, 18rem) minmax(0, 1fr);
  }

  .file-list {
    display: grid;
    gap: 0.4rem;
    max-height: 26rem;
    overflow: auto;
  }

  .file-list button {
    display: grid;
    gap: 0.22rem;
    justify-items: start;
    text-align: left;
  }

  .file-list strong,
  .file-list span,
  dd {
    overflow-wrap: anywhere;
  }

  .file-list span {
    color: #62707e;
    font-size: 0.78rem;
  }

  dl {
    display: grid;
    gap: 0.4rem;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    margin: 0.7rem 0;
  }

  dt {
    color: #667381;
    font-size: 0.74rem;
    text-transform: uppercase;
  }

  dd {
    margin: 0.1rem 0 0;
  }

  pre {
    background: #eef2f6;
    border-radius: 6px;
    color: #2d3844;
    margin: 0;
    max-height: 18rem;
    overflow: auto;
    padding: 0.8rem;
    white-space: pre-wrap;
  }

  .plain-list {
    color: #2d3844;
    margin: 0.65rem 0 0;
    padding-left: 1.2rem;
  }

  .empty {
    color: #667381;
  }

  @media (max-width: 980px) {
    .workspace,
    .files-grid,
    .toolbar,
    .toolbar form {
      grid-template-columns: 1fr;
    }

    .toolbar {
      align-items: stretch;
      display: grid;
    }
  }
</style>
