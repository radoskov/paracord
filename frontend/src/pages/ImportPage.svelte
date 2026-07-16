<!-- ImportPage — all paper-ingest paths (PDF upload, identifier lookup, server folder scan,
     BibTeX/RIS/CSL paste) behind a local sub-tab strip. Props: client (ApiClient).
     Non-obvious: sub-tab selection persists in sessionStorage; a pushed reference-graph citation
     (pendingImportText store) auto-switches to the citations sub-tab; multi-PDF import polls the
     staging batch (pollLoop) while extraction runs on the worker, merging in new item defaults
     without clobbering user checkbox choices. -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';

  import {
    ApiClient,
    type ServerImportRoot,
    type Source,
    type StagingBatch,
    type StagingItem,
  } from '../api/client';
  import BatchImport from '../components/BatchImport.svelte';
  import DraftReview from '../components/DraftReview.svelte';
  import ShelfPicker from '../components/ShelfPicker.svelte';
  import { pendingImportText } from '../lib/selection';
  import { isOwner } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  // Import sub-tabs: the page outgrew a single scroll of panels, so group them under a local
  // tab strip (same pattern as Admin #24). The last selected sub-tab survives page switches
  // within the browser session.
  type ImportTab = { id: string; label: string };
  const IMPORT_TABS: ImportTab[] = [
    { id: 'pdf', label: 'PDF import' },
    { id: 'citations', label: 'Citations' },
    { id: 'identifier', label: 'Identifier' },
    { id: 'folder', label: 'Folder import' },
    { id: 'external', label: 'External data' },
  ];
  const TAB_STORE_KEY = 'paracord_import_subtab';
  function initialTab(): string {
    try {
      const stored = sessionStorage.getItem(TAB_STORE_KEY);
      if (stored && IMPORT_TABS.some((t) => t.id === stored)) return stored;
    } catch {
      /* storage unavailable (private mode) — fall through to the default */
    }
    return 'pdf';
  }
  let activeTab = initialTab();
  function selectTab(id: string): void {
    activeTab = id;
    try {
      sessionStorage.setItem(TAB_STORE_KEY, id);
    } catch {
      /* storage unavailable — selection just won't survive a page switch */
    }
  }

  // Optional import-to-shelf targets per card (Phase J item 6); '' means no shelf.
  let uploadShelfId = '';
  let identifierShelfId = '';
  let bibtexShelfId = '';
  let risShelfId = '';
  let cslShelfId = '';

  let sources: Source[] = [];
  // The merged (yaml + owner-managed DB) import-root whitelist. Owner-only to list, so it stays
  // empty for editors — they type a configured alias the owner set up.
  let importRoots: ServerImportRoot[] = [];
  let newSourceName = '';
  let newSourceAlias = '';
  let selectedSourceId = '';
  let identifierValue = '';
  let bibtexContent = '';
  let risContent = '';
  let cslContent = '';
  let loading = false;
  let message = '';
  let warning = '';

  // Shown when an import succeeded but the server couldn't queue extraction (queue/Redis offline);
  // the file keeps its owed marker and the recovery sweep retries it (D7).
  const EXTRACTION_QUEUE_WARNING =
    "Imported, but extraction couldn't be queued — the processing queue looks offline. It'll retry automatically.";

  onMount(() => {
    void load();
    // A reference-graph "import citation" push prefills the citations box (BatchImport consumes
    // the store when it mounts) — jump to that sub-tab so the pushed text is actually visible.
    return pendingImportText.subscribe((val) => {
      if (val) activeTab = 'citations';
    });
  });

  async function run(fn: () => Promise<void>, ok?: string): Promise<void> {
    loading = true;
    message = '';
    warning = '';
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
      sources = await client.listSources();
      if (!selectedSourceId && sources[0]) selectedSourceId = sources[0].id;
      // Owners can see the merged whitelist (yaml-fixed + owner-managed) and pick an alias.
      if (get(isOwner)) {
        try {
          importRoots = await client.listServerImportRoots();
        } catch {
          importRoots = [];
        }
      }
    });
  }

  async function createSource(): Promise<void> {
    await run(async () => {
      const source = await client.createServerFolderSource({
        name: newSourceName,
        path_alias: newSourceAlias,
      });
      newSourceName = '';
      newSourceAlias = '';
      sources = await client.listSources();
      selectedSourceId = source.id;
    }, 'Source created');
  }

  async function importFolder(): Promise<void> {
    if (!selectedSourceId) return;
    await run(async () => {
      const batch = await client.importFolder(selectedSourceId);
      message = `Folder import ${batch.status}: ${batch.stats?.seen ?? 0} PDFs scanned, ${batch.stats?.created_works ?? 0} papers created`;
      if (batch.extraction_queued === false) warning = EXTRACTION_QUEUE_WARNING;
    });
  }

  // ---- Multi-PDF import (batch10 #1): extract before storing, preview, then choose. ----
  let multiFiles: File[] = [];
  let staging: StagingBatch | null = null;
  let accept: Record<string, boolean> = {};
  let commitResult: { created: number; skipped: number; warnings: string[] } | null = null;

  const BLOCKING = ['same_pdf', 'same_doi'] as const;
  function itemBlocked(item: StagingItem): string | null {
    for (const sig of BLOCKING) if (item.duplicates?.[sig]?.length) return sig;
    return null;
  }
  function dupWarnings(item: StagingItem): string[] {
    const d = item.duplicates ?? {};
    const out: string[] = [];
    if (d.same_pdf?.length) out.push('same PDF already in library');
    if (d.same_doi?.length) out.push('same DOI as an existing paper');
    if (d.same_title?.length) out.push('same title as an existing paper');
    return out;
  }

  function resetMulti(): void {
    multiFiles = [];
    staging = null;
    accept = {};
    commitResult = null;
  }

  // Seed the accept map: check extracted, non-blocked items by default.
  function seedAccept(batch: StagingBatch): void {
    const next: Record<string, boolean> = {};
    for (const item of batch.items) {
      next[item.id] = item.status === 'extracted' && !itemBlocked(item);
    }
    accept = next;
  }

  // Add default checkbox states for items that just finished extracting, without clobbering
  // the user's existing choices (the poll loop calls this on every tick).
  function mergeAccept(batch: StagingBatch): void {
    const next = { ...accept };
    for (const item of batch.items) {
      if (!(item.id in next)) next[item.id] = item.status === 'extracted' && !itemBlocked(item);
    }
    accept = next;
  }

  function summarizeDirect(batch: StagingBatch): void {
    const created = batch.items.filter((i) => i.created_work_id).length;
    const skipped = batch.items.filter((i) => i.status === 'skipped').length;
    commitResult = {
      created,
      skipped,
      warnings: batch.items
        .filter((i) => i.status === 'skipped' || i.status === 'extract_failed')
        .map((i) => `${i.filename}: ${i.error ?? dupWarnings(i).join(', ') ?? 'skipped'}`),
    };
    message = `Imported ${created} paper(s)${skipped ? `; skipped ${skipped}` : ''}.`;
  }

  // Live-updating, unbounded poll (S-batch item 2): the preview refreshes while extraction runs,
  // so already-extracted papers can be imported immediately; the server-side poll also self-heals
  // items whose worker died. Stops when the user resets the preview or the batch leaves
  // "extracting".
  let pollingBatchId: string | null = null;
  async function pollLoop(batchId: string, mode: 'preview' | 'direct'): Promise<void> {
    if (pollingBatchId === batchId) return;
    pollingBatchId = batchId;
    try {
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        if (!staging || staging.id !== batchId) return;
        let batch: StagingBatch;
        try {
          batch = await client.getStagingBatch(batchId);
        } catch {
          continue; // transient poll error — keep trying
        }
        mergeAccept(batch);
        staging = batch;
        if (batch.status !== 'extracting') break;
      }
      if (staging?.id === batchId && staging.status === 'committed' && mode === 'direct') {
        summarizeDirect(staging);
        multiFiles = [];
      }
    } finally {
      if (pollingBatchId === batchId) pollingBatchId = null;
    }
  }

  async function startMultiImport(mode: 'preview' | 'direct'): Promise<void> {
    if (!multiFiles.length) return;
    commitResult = null;
    await run(async () => {
      const batch = await client.uploadPdfsMulti(multiFiles, mode, uploadShelfId || null);
      staging = batch;
      multiFiles = [];
      if (batch.status === 'committed') {
        summarizeDirect(batch);
        return;
      }
      seedAccept(batch);
      if (!batch.extraction_queued) warning = EXTRACTION_QUEUE_WARNING;
      if (batch.status === 'extracting') void pollLoop(batch.id, mode);
    });
  }

  // partial=true: import ONLY the checked, already-extracted items and leave the rest undecided
  // (the batch stays open — usable repeatedly while extraction continues). partial=false: the
  // classic closing commit — accept checked, skip everything else.
  async function commitSelected(partial: boolean): Promise<void> {
    if (!staging) return;
    const batchId = staging.id;
    const decisions = partial
      ? staging.items
          .filter((i) => i.status === 'extracted' && accept[i.id])
          .map((i) => ({ item_id: i.id, action: 'accept' as const }))
      : staging.items
          .filter((i) => !['committed', 'skipped'].includes(i.status))
          .map((i) => ({
            item_id: i.id,
            action: (accept[i.id] ? 'accept' : 'skip') as 'accept' | 'skip',
          }));
    if (!decisions.length) return;
    await run(async () => {
      const result = await client.commitStagingBatch(batchId, { decisions });
      commitResult = result;
      message = `Created ${result.created} paper(s)${result.skipped ? `; skipped ${result.skipped}` : ''}.`;
      staging = await client.getStagingBatch(batchId);
      if (staging.status === 'extracting') void pollLoop(batchId, 'preview');
    });
  }

  const ARXIV_ID_RE = /^\d{4}\.\d{4,5}(v\d+)?$|^[a-z-]+\/\d{7}(v\d+)?$/i;

  async function importIdentifier(): Promise<void> {
    const value = identifierValue.trim();
    if (!value) return;
    await run(async () => {
      const isArxiv = ARXIV_ID_RE.test(value);
      const result = await client.importByIdentifier(
        isArxiv ? 'arxiv' : 'doi',
        value,
        identifierShelfId || null,
      );
      identifierValue = '';
      identifierReview?.reset();
      message = result.created
        ? `Imported as ${isArxiv ? 'arXiv' : 'DOI'} (${result.enriched_sources.join(', ') || 'no enrichment'})`
        : 'Already in the library — re-enriched';
    });
  }

  // Identifier preview-&-choose (UX batch): fetch the metadata WITHOUT creating anything, let the
  // user revise it in the shared draft table, then commit through the batch commit endpoint.
  let identifierReview: DraftReview;
  async function previewIdentifier(): Promise<void> {
    const value = identifierValue.trim();
    if (!value) return;
    await run(async () => {
      const isArxiv = ARXIV_ID_RE.test(value);
      const preview = await client.externalPreview(isArxiv ? { arxiv: value } : { doi: value });
      if (!preview.available) {
        message = preview.message || 'No metadata found for this identifier.';
        return;
      }
      identifierReview.reset();
      identifierReview.addDrafts([
        {
          line_index: 0,
          raw_line: value,
          engine: 'identifier',
          suggested_title: preview.title,
          suggested_authors: preview.authors ?? [],
          suggested_year: preview.year,
          suggested_doi: preview.doi ?? (isArxiv ? null : value),
          suggested_venue: preview.venue,
          suggested_abstract: preview.abstract,
          match_status: preview.title ? 'matched' : 'title_only',
          candidates: [],
          suggested_arxiv_id: preview.arxiv_id ?? (isArxiv ? value : null),
          suggested_work_type: null,
          existing_work_id: null,
        },
      ]);
      if (preview.sources.length) message = `Fetched from ${preview.sources.join(', ')}.`;
    });
  }

  function onIdentifierCommitted(event: CustomEvent<{ remaining: number }>): void {
    if (event.detail.remaining === 0) identifierValue = '';
  }

  async function importBibtex(): Promise<void> {
    if (!bibtexContent.trim()) return;
    await run(async () => {
      const batch = await client.importBibtex(bibtexContent, bibtexShelfId || null);
      bibtexContent = '';
      bibtexReview?.reset();
      message = `BibTeX import: ${batch.stats?.created ?? 0} created, ${batch.stats?.matched ?? 0} matched`;
    });
  }

  // BibTeX preview-&-choose (UX batch): parse without writing, review/uncheck/edit the parsed
  // entries in the shared draft table, then commit through the batch commit endpoint.
  let bibtexReview: DraftReview;
  async function previewBibtexEntries(): Promise<void> {
    if (!bibtexContent.trim()) return;
    await run(async () => {
      const result = await client.bibtexImportPreview(bibtexContent);
      bibtexReview.reset();
      bibtexReview.addDrafts(result.drafts);
      if (!result.drafts.length) message = 'No BibTeX entries found in the pasted text.';
    });
  }

  function onBibtexCommitted(event: CustomEvent<{ remaining: number }>): void {
    if (event.detail.remaining === 0) bibtexContent = '';
  }

  async function importRis(): Promise<void> {
    if (!risContent.trim()) return;
    await run(async () => {
      const batch = await client.importRis(risContent, risShelfId || null);
      risContent = '';
      message = `RIS import: ${batch.stats?.created ?? 0} created, ${batch.stats?.matched ?? 0} matched`;
    });
  }

  async function importCsl(): Promise<void> {
    if (!cslContent.trim()) return;
    await run(async () => {
      const batch = await client.importCsl(cslContent, cslShelfId || null);
      cslContent = '';
      message = `CSL import: ${batch.stats?.created ?? 0} created, ${batch.stats?.matched ?? 0} matched`;
    });
  }
</script>

<section>
  <nav class="import-tabs" aria-label="Import methods">
    {#each IMPORT_TABS as tab (tab.id)}
      <button type="button" class="import-tab" class:active={activeTab === tab.id}
        on:click={() => selectTab(tab.id)}>{tab.label}</button>
    {/each}
  </nav>

  <div class="grid">
  {#if message}<p class="muted msg">{message}</p>{/if}
  {#if warning}<p class="msg warn-msg" role="alert">⚠ {warning}</p>{/if}

  {#if activeTab === 'pdf'}
  <div class="card wide-card">
    <h2>Upload PDFs</h2>
    <p class="muted">
      Add one or more PDFs — each becomes its own paper. <strong>Preview &amp; choose</strong>
      extracts every PDF first, shows the metadata and any collisions, and lets you pick which papers
      to create. <strong>Import directly</strong> creates them straight away (a duplicate PDF/DOI or a
      failed extraction is skipped with a note). GROBID extraction runs in the background.
    </p>
    <input type="file" accept=".pdf,application/pdf" multiple
      on:change={(e) => (multiFiles = Array.from(e.currentTarget.files ?? []))}
      aria-label="PDF files" />
    <ShelfPicker {client} bind:value={uploadShelfId} label="Add to shelf (optional)" />
    <div class="row">
      <button type="button" on:click={() => startMultiImport('preview')}
        disabled={!multiFiles.length || loading}
        title={multiFiles.length ? 'Extract and preview before creating papers' : 'Choose PDF(s) first'}
        >Preview &amp; choose</button>
      <button type="button" class="secondary" on:click={() => startMultiImport('direct')}
        disabled={!multiFiles.length || loading}
        title={multiFiles.length ? 'Create papers directly (skips duplicates/errors)' : 'Choose PDF(s) first'}
        >Import directly</button>
    </div>
    {#if multiFiles.length}<p class="hintline">{multiFiles.length} file(s) selected.</p>{/if}

    {#if staging && staging.status !== 'committed'}
      <div class="preview-table" role="table" aria-label="Extraction preview">
        <div class="preview-head" role="row">
          <span>Create</span><span>File / title</span><span>Details</span><span>Status</span>
        </div>
        {#each staging.items.filter((i) => !['committed', 'skipped'].includes(i.status)) as item (item.id)}
          {@const blocked = itemBlocked(item)}
          {@const warns = dupWarnings(item)}
          <div class="preview-row" role="row">
            <span role="cell">
              <input type="checkbox" bind:checked={accept[item.id]}
                disabled={item.status !== 'extracted'} aria-label={`Create paper from ${item.filename}`} />
            </span>
            <span role="cell">
              <strong>{item.parsed?.title || item.filename}</strong>
              <span class="muted small">{item.filename}</span>
            </span>
            <span role="cell" class="small">
              {#if item.parsed?.authors?.length}<span>{item.parsed.authors.slice(0, 4).join('; ')}</span>{/if}
              {#if item.parsed?.year}<span> · {item.parsed.year}</span>{/if}
              {#if item.parsed?.doi}<span> · doi:{item.parsed.doi}</span>{/if}
              {#each warns as w}<span class="dup">⚠ {w}</span>{/each}
            </span>
            <span role="cell" class="small">
              {#if item.status === 'extract_failed'}<span class="dup">extraction failed{item.error ? `: ${item.error}` : ''}</span>
              {:else if blocked}<span class="dup">blocked ({blocked.replace('_', ' ')})</span>
              {:else}{item.status}{/if}
            </span>
          </div>
        {/each}
      </div>
      {#if staging.items.some((i) => i.status === 'committed')}
        <p class="hintline">
          {staging.items.filter((i) => i.status === 'committed').length} paper(s) imported from this
          batch so far.
        </p>
      {/if}
      {#if staging.status === 'extracting'}
        <p class="hintline">Extracting… this updates automatically. You can already import the
          extracted papers below — the rest keep processing.</p>
        <div class="row">
          <button type="button" on:click={() => commitSelected(true)}
            disabled={loading || !staging.items.some((i) => i.status === 'extracted' && accept[i.id])}
            title="Import the checked, already-extracted papers now; the rest keep processing"
            >Import selected now</button>
          <button type="button" class="secondary" on:click={resetMulti} disabled={loading}>Cancel</button>
        </div>
      {:else}
        <div class="row">
          <button type="button" on:click={() => commitSelected(false)} disabled={loading}
            title="Create the checked papers (unchecked ones are skipped)">Create selected papers</button>
          <button type="button" class="secondary" on:click={resetMulti} disabled={loading}>Cancel</button>
        </div>
      {/if}
    {/if}

    {#if commitResult}
      <div class="commit-result">
        <p class="msg">Created {commitResult.created} paper(s){commitResult.skipped ? `; skipped ${commitResult.skipped}` : ''}.</p>
        {#if commitResult.warnings.length}
          <ul class="warn-list">
            {#each commitResult.warnings as w}<li>{w}</li>{/each}
          </ul>
        {/if}
        <button type="button" class="secondary" on:click={resetMulti}>Import more</button>
      </div>
    {/if}
  </div>
  {/if}

  {#if activeTab === 'identifier'}
  <div class="card narrow-card">
    <h2>Import by identifier</h2>
    <p class="muted">
      Fetch metadata for an arXiv id or DOI. <strong>Preview &amp; choose</strong> shows what was
      found and lets you revise the record before creating the paper; <strong>Import
      directly</strong> creates it straight away (idempotent).
    </p>
    <form on:submit|preventDefault={previewIdentifier} class="stack">
      <input bind:value={identifierValue} placeholder="e.g. 1706.03762 or 10.1145/3292500" aria-label="arXiv id or DOI" />
      <ShelfPicker {client} bind:value={identifierShelfId} label="Add to shelf on direct import (optional)" />
      <div class="row">
        <button type="submit" disabled={!identifierValue.trim() || loading}
          title={identifierValue.trim() ? 'Fetch the metadata and review it before creating the paper' : 'Enter an arXiv id or DOI first'}
          >Preview &amp; choose</button>
        <button type="button" class="secondary" on:click={importIdentifier}
          disabled={!identifierValue.trim() || loading}
          title={identifierValue.trim() ? 'Fetch metadata and create the paper straight away' : 'Enter an arXiv id or DOI first'}
          >Import directly</button>
      </div>
    </form>
    <DraftReview bind:this={identifierReview} {client} on:committed={onIdentifierCommitted} />
  </div>
  {/if}

  {#if activeTab === 'folder'}
  <div class="card narrow-card">
    <h2>Server folder</h2>
    <p class="muted">
      Scans a folder <strong>on the server machine</strong> for PDFs. For security the server
      won’t read arbitrary paths — the whitelist of folders (each with an <em>alias</em>) comes
      from <code>storage.server_allowed_roots</code> in <code>server.yaml</code> plus any the owner
      adds under <em>Admin → Server import folders</em>. Enter a configured alias below. <br />
      <strong>Files on your own computer?</strong> Use a local <strong>agent</strong> instead
      (see the <em>Admin → Agents</em> tab) — server-folder can’t reach your PC.
    </p>
    <form on:submit|preventDefault={createSource} class="stack">
      <input bind:value={newSourceName} placeholder="Source name" aria-label="Source name" />
      <input
        bind:value={newSourceAlias}
        placeholder="Configured alias"
        aria-label="Configured alias"
        list={importRoots.length ? 'server-import-roots' : undefined}
        title="The alias of a whitelisted server folder (configured in server.yaml or under Admin → Server import folders)"
      />
      {#if importRoots.length}
        <datalist id="server-import-roots">
          {#each importRoots as root (root.alias)}
            <option value={root.alias}>{root.path}</option>
          {/each}
        </datalist>
      {/if}
      <button type="submit" disabled={!newSourceName.trim() || !newSourceAlias.trim() || loading}
        title="Create a server-folder source from a configured alias">Add source</button>
    </form>
    {#if importRoots.length}
      <p class="hintline">
        Available aliases:
        {#each importRoots as root, i (root.alias)}<code>{root.alias}</code>{#if !root.exists} <span class="warn" title="This folder does not currently exist on the server">(missing)</span>{/if}{#if i < importRoots.length - 1}, {/if}{/each}
      </p>
    {/if}
    <div class="row top">
      <select bind:value={selectedSourceId} aria-label="Source to import" title="Server-folder source to scan">
        <option value="">Choose a source…</option>
        {#each sources as source (source.id)}<option value={source.id}>{source.name}</option>{/each}
      </select>
      <button type="button" on:click={importFolder} disabled={!selectedSourceId || loading}
        title={selectedSourceId ? 'Scan this folder for PDFs' : 'Create or choose a source first'}>Import folder</button>
    </div>
    {#if sources.length === 0}<p class="hintline">No sources yet — add one above. Aliases come from the server config.</p>{/if}
  </div>
  {/if}

  {#if activeTab === 'citations'}
  <div class="card wide">
    <BatchImport {client} />
  </div>

  <div class="card wide">
    <h2>Paste BibTeX</h2>
    <p class="muted">
      Paste one or more BibTeX entries. <strong>Preview &amp; choose</strong> parses them first and
      lets you review, edit and pick the entries to create (existing papers are flagged).
      <strong>Import directly</strong> creates them straight away — duplicates (by DOI/title) are
      matched instead of duplicated.
    </p>
    <form on:submit|preventDefault={previewBibtexEntries} class="stack">
      <textarea bind:value={bibtexContent} rows="5" placeholder="@article&#123;...&#125;" aria-label="BibTeX"></textarea>
      <ShelfPicker {client} bind:value={bibtexShelfId} label="Add to shelf on direct import (optional)" />
      <div class="row">
        <button type="submit" disabled={!bibtexContent.trim() || loading}
          title={bibtexContent.trim() ? 'Parse and review the entries before creating papers' : 'Paste BibTeX first'}
          >Preview &amp; choose</button>
        <button type="button" class="secondary" on:click={importBibtex}
          disabled={!bibtexContent.trim() || loading}
          title={bibtexContent.trim() ? 'Import the pasted BibTeX entries straight away' : 'Paste BibTeX first'}
          >Import directly</button>
      </div>
    </form>
    <DraftReview bind:this={bibtexReview} {client} on:committed={onBibtexCommitted} />
  </div>
  {/if}

  {#if activeTab === 'external'}
  <div class="card">
    <h2>Paste RIS</h2>
    <p class="muted">Reference Manager / EndNote format (tagged lines, one record per <code>ER</code>).</p>
    <form on:submit|preventDefault={importRis} class="stack">
      <textarea bind:value={risContent} rows="5" placeholder="TY  - JOUR&#10;TI  - Title&#10;ER  -" aria-label="RIS"></textarea>
      <ShelfPicker {client} bind:value={risShelfId} label="Add to shelf (optional)" />
      <button type="submit" disabled={!risContent.trim() || loading}
        title={risContent.trim() ? 'Import the pasted RIS records' : 'Paste RIS first'}>Import RIS</button>
    </form>
  </div>

  <div class="card">
    <h2>Paste CSL JSON</h2>
    <p class="muted">Citation Style Language JSON (an array of items, as exported by Zotero).</p>
    <form on:submit|preventDefault={importCsl} class="stack">
      <textarea bind:value={cslContent} rows="5" placeholder={'[{"title": "…", "DOI": "…"}]'} aria-label="CSL JSON"></textarea>
      <ShelfPicker {client} bind:value={cslShelfId} label="Add to shelf (optional)" />
      <button type="submit" disabled={!cslContent.trim() || loading}
        title={cslContent.trim() ? 'Import the pasted CSL JSON items' : 'Paste CSL JSON first'}>Import CSL JSON</button>
    </form>
  </div>
  {/if}
  </div>
</section>

<style>
  .grid {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  }

  .import-tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    margin: 0 0 1rem;
  }

  .import-tab {
    background: var(--surface-overlay);
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    color: var(--accent-secondary);
    font-weight: 600;
  }

  .import-tab.active {
    background: var(--accent-primary);
    border-color: var(--accent-primary);
    color: var(--ink-inverse);
  }

  /* Single-form tabs (identifier, folder): cap the card so a lone form doesn't stretch
     across the whole viewport. */
  .narrow-card {
    max-width: 44rem;
  }

  .warn {
    color: var(--status-warning);
    font-weight: 600;
  }

  .msg {
    grid-column: 1 / -1;
    margin: 0;
  }

  .warn-msg {
    background: var(--status-warning-bg);
    border: 1px solid var(--status-warning-border);
    border-radius: 6px;
    color: var(--status-warning);
    font-weight: 600;
    padding: 0.5rem 0.75rem;
  }

  .wide {
    grid-column: 1 / -1;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0 0 0.4rem;
  }

  .stack {
    display: grid;
    gap: 0.5rem;
  }

  .row {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .top {
    margin-top: 0.6rem;
  }

  .card input[type='file'] {
    margin-bottom: 0.5rem;
    width: 100%;
  }

  textarea {
    resize: vertical;
  }

  /* The multi-PDF card spans the full grid so its preview table has room. */
  .wide-card {
    grid-column: 1 / -1;
  }

  .small {
    color: var(--ink-muted);
    font-size: 0.8rem;
  }

  .preview-table {
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    margin-top: 0.75rem;
    overflow: hidden;
  }

  .preview-head,
  .preview-row {
    align-items: start;
    display: grid;
    gap: 0.5rem;
    grid-template-columns: 3.5rem minmax(0, 2fr) minmax(0, 3fr) 8rem;
    padding: 0.4rem 0.6rem;
  }

  .preview-head {
    background: var(--surface-sunken);
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
  }

  .preview-row {
    border-top: 1px solid var(--border-normal);
  }

  .preview-row strong {
    display: block;
  }

  .dup {
    color: var(--status-warning);
    display: inline-block;
    margin-right: 0.4rem;
  }

  .warn-list {
    color: var(--status-warning);
    font-size: 0.85rem;
    margin: 0.25rem 0;
    padding-left: 1.2rem;
  }

  .commit-result {
    margin-top: 0.75rem;
  }
</style>
