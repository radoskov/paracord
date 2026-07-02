<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';

  import { ApiClient, type ServerImportRoot, type Source } from '../api/client';
  import BatchImport from '../components/BatchImport.svelte';
  import ShelfPicker from '../components/ShelfPicker.svelte';
  import { isOwner } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

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
  let uploadFile: File | null = null;
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

  onMount(load);

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

  async function upload(): Promise<void> {
    if (!uploadFile) return;
    const file = uploadFile;
    await run(async () => {
      const batch = await client.uploadPdf(file, uploadShelfId || null);
      uploadFile = null;
      const queued = batch.extraction_queued !== false;
      message = `Uploaded “${file.name}” (batch ${batch.status})${queued ? '; extraction queued' : ''}`;
      if (!queued) warning = EXTRACTION_QUEUE_WARNING;
    });
  }

  async function importIdentifier(): Promise<void> {
    const value = identifierValue.trim();
    if (!value) return;
    await run(async () => {
      const isArxiv = /^\d{4}\.\d{4,5}(v\d+)?$|^[a-z-]+\/\d{7}(v\d+)?$/i.test(value);
      const result = await client.importByIdentifier(
        isArxiv ? 'arxiv' : 'doi',
        value,
        identifierShelfId || null,
      );
      identifierValue = '';
      message = result.created
        ? `Imported as ${isArxiv ? 'arXiv' : 'DOI'} (${result.enriched_sources.join(', ') || 'no enrichment'})`
        : 'Already in the library — re-enriched';
    });
  }

  async function importBibtex(): Promise<void> {
    if (!bibtexContent.trim()) return;
    await run(async () => {
      const batch = await client.importBibtex(bibtexContent, bibtexShelfId || null);
      bibtexContent = '';
      message = `BibTeX import: ${batch.stats?.created ?? 0} created, ${batch.stats?.matched ?? 0} matched`;
    });
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

<section class="grid">
  {#if message}<p class="muted msg">{message}</p>{/if}
  {#if warning}<p class="msg warn-msg" role="alert">⚠ {warning}</p>{/if}

  <div class="card">
    <h2>Upload a PDF</h2>
    <p class="muted">Store a PDF in the managed library; GROBID extraction runs in the background.</p>
    <input type="file" accept=".pdf,application/pdf"
      on:change={(e) => (uploadFile = e.currentTarget.files?.[0] ?? null)} aria-label="PDF file" />
    <ShelfPicker {client} bind:value={uploadShelfId} label="Add to shelf (optional)" />
    <button type="button" on:click={upload} disabled={!uploadFile || loading}
      title={uploadFile ? 'Upload the chosen PDF' : 'Choose a PDF file first'}>Upload PDF</button>
    {#if !uploadFile}<p class="hintline">Choose a PDF to enable “Upload PDF”.</p>{/if}
  </div>

  <div class="card">
    <h2>Import by identifier</h2>
    <p class="muted">Fetch metadata for an arXiv id or DOI and create a paper (idempotent).</p>
    <form on:submit|preventDefault={importIdentifier} class="stack">
      <div class="row">
        <input bind:value={identifierValue} placeholder="e.g. 1706.03762 or 10.1145/3292500" aria-label="arXiv id or DOI" />
        <button type="submit" disabled={!identifierValue.trim() || loading}
          title={identifierValue.trim() ? 'Fetch metadata and create the paper' : 'Enter an arXiv id or DOI first'}>Import</button>
      </div>
      <ShelfPicker {client} bind:value={identifierShelfId} label="Add to shelf (optional)" />
    </form>
  </div>

  <div class="card">
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

  <div class="card wide">
    <BatchImport {client} />
  </div>

  <div class="card wide">
    <h2>Paste BibTeX</h2>
    <p class="muted">Paste one or more BibTeX entries; duplicates (by DOI/title) are skipped.</p>
    <form on:submit|preventDefault={importBibtex} class="stack">
      <textarea bind:value={bibtexContent} rows="5" placeholder="@article&#123;...&#125;" aria-label="BibTeX"></textarea>
      <ShelfPicker {client} bind:value={bibtexShelfId} label="Add to shelf (optional)" />
      <button type="submit" disabled={!bibtexContent.trim() || loading}
        title={bibtexContent.trim() ? 'Import the pasted BibTeX entries' : 'Paste BibTeX first'}>Import BibTeX</button>
    </form>
  </div>

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
</section>

<style>
  .grid {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  }

  .warn {
    color: #b45309;
    font-weight: 600;
  }

  .msg {
    grid-column: 1 / -1;
    margin: 0;
  }

  .warn-msg {
    background: #fff7ed;
    border: 1px solid #fdba74;
    border-radius: 6px;
    color: #7c2d12;
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
</style>
