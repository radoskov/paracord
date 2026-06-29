<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiClient, type Source } from '../api/client';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let sources: Source[] = [];
  let newSourceName = '';
  let newSourceAlias = '';
  let selectedSourceId = '';
  let uploadFile: File | null = null;
  let identifierValue = '';
  let bibtexContent = '';
  let loading = false;
  let message = '';

  onMount(load);

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
      sources = await client.listSources();
      if (!selectedSourceId && sources[0]) selectedSourceId = sources[0].id;
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
      message = `Folder import ${batch.status}: ${batch.stats?.seen ?? 0} PDFs scanned, ${batch.stats?.created_works ?? 0} works created`;
    });
  }

  async function upload(): Promise<void> {
    if (!uploadFile) return;
    const file = uploadFile;
    await run(async () => {
      const batch = await client.uploadPdf(file);
      uploadFile = null;
      message = `Uploaded “${file.name}” (batch ${batch.status}); extraction queued`;
    });
  }

  async function importIdentifier(): Promise<void> {
    const value = identifierValue.trim();
    if (!value) return;
    await run(async () => {
      const isArxiv = /^\d{4}\.\d{4,5}(v\d+)?$|^[a-z-]+\/\d{7}(v\d+)?$/i.test(value);
      const result = await client.importByIdentifier(isArxiv ? 'arxiv' : 'doi', value);
      identifierValue = '';
      message = result.created
        ? `Imported as ${isArxiv ? 'arXiv' : 'DOI'} (${result.enriched_sources.join(', ') || 'no enrichment'})`
        : 'Already in the library — re-enriched';
    });
  }

  async function importBibtex(): Promise<void> {
    if (!bibtexContent.trim()) return;
    await run(async () => {
      const batch = await client.importBibtex(bibtexContent);
      bibtexContent = '';
      message = `BibTeX import: ${batch.stats?.created ?? 0} created, ${batch.stats?.matched ?? 0} matched`;
    });
  }
</script>

<section class="grid">
  {#if message}<p class="muted msg">{message}</p>{/if}

  <div class="card">
    <h2>Upload a PDF</h2>
    <p class="muted">Store a PDF in the managed library; GROBID extraction runs in the background.</p>
    <input type="file" accept=".pdf,application/pdf"
      on:change={(e) => (uploadFile = e.currentTarget.files?.[0] ?? null)} aria-label="PDF file" />
    <button type="button" on:click={upload} disabled={!uploadFile || loading}
      title={uploadFile ? 'Upload the chosen PDF' : 'Choose a PDF file first'}>Upload PDF</button>
    {#if !uploadFile}<p class="hintline">Choose a PDF to enable “Upload PDF”.</p>{/if}
  </div>

  <div class="card">
    <h2>Import by identifier</h2>
    <p class="muted">Fetch metadata for an arXiv id or DOI and create a work (idempotent).</p>
    <form on:submit|preventDefault={importIdentifier} class="row">
      <input bind:value={identifierValue} placeholder="e.g. 1706.03762 or 10.1145/3292500" aria-label="arXiv id or DOI" />
      <button type="submit" disabled={!identifierValue.trim() || loading}>Import</button>
    </form>
  </div>

  <div class="card">
    <h2>Server folder</h2>
    <p class="muted">Register a configured server-folder root (by alias) and scan it for PDFs.</p>
    <form on:submit|preventDefault={createSource} class="stack">
      <input bind:value={newSourceName} placeholder="Source name" aria-label="Source name" />
      <input bind:value={newSourceAlias} placeholder="Configured alias" aria-label="Configured alias" />
      <button type="submit" disabled={!newSourceName.trim() || !newSourceAlias.trim() || loading}
        title="Create a server-folder source from a configured alias">Add source</button>
    </form>
    <div class="row top">
      <select bind:value={selectedSourceId} aria-label="Source to import">
        <option value="">Choose a source…</option>
        {#each sources as source (source.id)}<option value={source.id}>{source.name}</option>{/each}
      </select>
      <button type="button" on:click={importFolder} disabled={!selectedSourceId || loading}
        title={selectedSourceId ? 'Scan this folder for PDFs' : 'Create or choose a source first'}>Import folder</button>
    </div>
    {#if sources.length === 0}<p class="hintline">No sources yet — add one above. Aliases come from the server config.</p>{/if}
  </div>

  <div class="card wide">
    <h2>Paste BibTeX</h2>
    <p class="muted">Paste one or more BibTeX entries; duplicates (by DOI/title) are skipped.</p>
    <form on:submit|preventDefault={importBibtex} class="stack">
      <textarea bind:value={bibtexContent} rows="5" placeholder="@article&#123;...&#125;" aria-label="BibTeX"></textarea>
      <button type="submit" disabled={!bibtexContent.trim() || loading}>Import BibTeX</button>
    </form>
    <p class="hintline">RIS and CSL-JSON import are coming in a follow-up.</p>
  </div>
</section>

<style>
  .grid {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  }

  .msg {
    grid-column: 1 / -1;
    margin: 0;
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
