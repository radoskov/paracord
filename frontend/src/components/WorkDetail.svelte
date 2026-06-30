<script lang="ts">
  import { onDestroy } from 'svelte';

  import {
    ApiClient,
    type Annotation,
    type AnnotationCreate,
    type CitationContext,
    type FieldReview,
    type ReferenceRecord,
    type Summary,
    type Tag,
    type Work,
    type WorkFile,
  } from '../api/client';
  import { canEdit, INSUFFICIENT_ROLE } from '../lib/session';
  import { errorMessage, formatBytes } from '../lib/ui';
  import Modal from './Modal.svelte';
  import PdfReader from './PdfReader.svelte';

  export let client: ApiClient;
  export let work: Work;
  export let onUpdated: (work: Work) => void = () => {};
  export let onClose: () => void = () => {};
  export let onDeleted: (workId: string) => void = () => {};
  export let onImported: () => void = () => {};

  const STATUSES = ['unread', 'skimmed', 'reading', 'read', 'important', 'revisit'];

  let loadedId = '';
  let loading = false;
  let message = '';

  // editable fields
  let form = { canonical_title: '', year: '', venue: '', doi: '', arxiv_id: '', abstract: '', reading_status: 'unread' };

  let fields: FieldReview[] = [];
  let files: WorkFile[] = [];
  let contexts: CitationContext[] = [];
  let references: ReferenceRecord[] = [];
  let annotations: Annotation[] = [];
  let summaries: Summary[] = [];
  let tags: Tag[] = [];
  let applyTagId = '';
  let attachFile: File | null = null;

  let readerFile: WorkFile | null = null;
  let readerUrl: string | null = null;
  let showReader = false;
  // When the reader is opened to jump straight to a reference's in-text mentions.
  let readerJumpReferenceId: string | null = null;
  // Reference entry to scroll-to + flash when a citation overlay is clicked in the reader.
  let flashRefId = '';

  $: if (work && work.id !== loadedId) void loadDetail(work);

  // Reference ids that have at least one in-text mention with page coordinates — these can
  // be located in the reader via "Find in text".
  $: locatableReferenceIds = new Set(
    contexts
      .filter((c) => c.reference_id && (c.pdf_coordinates?.length ?? 0) > 0)
      .map((c) => c.reference_id),
  );

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

  async function loadDetail(w: Work): Promise<void> {
    loadedId = w.id;
    clearReader();
    form = {
      canonical_title: w.canonical_title ?? '',
      year: w.year ? String(w.year) : '',
      venue: w.venue ?? '',
      doi: w.doi ?? '',
      arxiv_id: w.arxiv_id ?? '',
      abstract: w.abstract ?? '',
      reading_status: w.reading_status,
    };
    await run(async () => {
      [fields, files, contexts, references, annotations, summaries, tags] = await Promise.all([
        client.listWorkMetadata(w.id),
        client.listWorkFiles(w.id),
        client.listCitationContexts(w.id),
        client.listWorkReferences(w.id),
        client.listAnnotations(w.id),
        client.listSummaries(w.id),
        client.listTags(),
      ]);
    });
  }

  async function save(): Promise<void> {
    await run(async () => {
      const updated = await client.updateWork(work.id, {
        canonical_title: form.canonical_title || null,
        year: form.year ? Number(form.year) : null,
        venue: form.venue || null,
        doi: form.doi || null,
        arxiv_id: form.arxiv_id || null,
        abstract: form.abstract || null,
        reading_status: form.reading_status as Work['reading_status'],
      });
      onUpdated(updated);
      fields = await client.listWorkMetadata(work.id);
    }, 'Saved');
  }

  let related: Work[] = [];
  let relatedLoaded = false;
  async function loadRelated(): Promise<void> {
    await run(async () => {
      related = await client.getRelatedWorks(work.id, 8);
      relatedLoaded = true;
    });
  }

  async function importReference(referenceId: string): Promise<void> {
    await run(async () => {
      await client.importReferenceAsWork(referenceId);
      references = await client.listWorkReferences(work.id);
      onImported();
    }, 'Reference imported into the library');
  }

  async function exportNotes(): Promise<void> {
    await run(async () => {
      const r = await client.exportAnnotations(work.id, 'markdown');
      const url = URL.createObjectURL(new Blob([r.content], { type: r.content_type }));
      const a = document.createElement('a');
      a.href = url;
      a.download = r.filename;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  async function enrich(): Promise<void> {
    await run(async () => {
      const result = await client.enrichWork(work.id);
      message =
        `Enrichment ${result.status} (job ${(result.job_id ?? '').slice(0, 8) || 'n/a'}). ` +
        'It runs in the background worker — watch the Jobs tab for progress.';
    });
  }

  async function deletePaper(): Promise<void> {
    const title = form.canonical_title || 'this paper';
    if (!window.confirm(`Delete “${title}”? Its files stay in the library; links and notes are removed.`))
      return;
    const id = work.id;
    await run(async () => {
      await client.deleteWork(id);
      onDeleted(id);
    });
  }

  async function toggleLock(fieldName: string, confirmed: boolean): Promise<void> {
    await run(async () => {
      await client.confirmMetadataField(work.id, fieldName, confirmed);
      fields = await client.listWorkMetadata(work.id);
    }, confirmed ? 'Field locked' : 'Field unlocked');
  }

  async function selectCanonical(assertionId: string): Promise<void> {
    await run(async () => {
      const updated = await client.selectMetadataAssertion(work.id, assertionId);
      onUpdated(updated);
      await loadDetail(updated);
    }, 'Canonical value updated');
  }

  async function upload(): Promise<void> {
    if (!attachFile) return;
    const file = attachFile;
    await run(async () => {
      await client.uploadWorkFile(work.id, file);
      attachFile = null;
      files = await client.listWorkFiles(work.id);
    }, `Attached “${file.name}”; extraction queued`);
  }

  async function reextract(file: WorkFile): Promise<void> {
    await run(async () => {
      const result = await client.extractFile(file.id);
      files = await client.listWorkFiles(work.id);
      message =
        `Extraction ${result.status} (job ${(result.job_id ?? '').slice(0, 8) || 'n/a'}). ` +
        'It runs in the background worker — watch the Jobs tab.';
    });
  }

  function fileStatusLabel(status: string): string {
    return (
      {
        extracted: 'extracted ✓',
        extract_failed: 'extraction failed',
        available: 'not extracted',
        extracted_discarded: 'extracted — PDF not stored on server',
      }[status] ?? status
    );
  }

  async function copyHash(sha: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(sha);
      message = 'Content hash copied';
    } catch {
      message = sha;
    }
  }

  async function openInReader(file: WorkFile, jumpReferenceId: string | null = null): Promise<void> {
    await run(async () => {
      const blob = await client.getFileBlob(file.id);
      clearReader();
      readerUrl = URL.createObjectURL(blob);
      readerFile = file;
      readerJumpReferenceId = jumpReferenceId;
      showReader = true;
    });
  }

  // Reference "Find in text" → open the reader and jump to the reference's first in-text mention.
  function findReferenceInText(referenceId: string): void {
    const file = readerFile ?? files[0];
    if (file) void openInReader(file, referenceId);
  }

  // Citation overlay clicked in the reader → reveal + flash the matching reference entry.
  function navigateToReference(referenceId: string): void {
    const detailsEl = document.querySelector('details.references-block') as HTMLDetailsElement | null;
    if (detailsEl) detailsEl.open = true;
    flashRefId = referenceId;
    window.setTimeout(() => {
      const el = document.getElementById(`ref-${referenceId}`);
      el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }, 0);
    window.setTimeout(() => {
      if (flashRefId === referenceId) flashRefId = '';
    }, 2000);
  }

  async function openInNewTab(file: WorkFile): Promise<void> {
    await run(async () => {
      const blob = await client.getFileBlob(file.id);
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener,noreferrer');
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    });
  }

  function closeReader(): void {
    showReader = false;
    clearReader();
  }

  async function applyTag(): Promise<void> {
    if (!applyTagId) return;
    await run(async () => {
      await client.addTagLink(applyTagId, 'work', work.id);
    }, 'Tag applied');
  }

  async function removeTag(): Promise<void> {
    if (!applyTagId) return;
    await run(async () => {
      await client.removeTagLink(applyTagId, 'work', work.id);
    }, 'Tag removed');
  }

  async function createAnnotation(payload: AnnotationCreate): Promise<void> {
    await run(async () => {
      await client.createAnnotation(work.id, {
        ...payload,
        file_id: readerFile?.id ?? files[0]?.id ?? null,
      });
      annotations = await client.listAnnotations(work.id);
    }, 'Annotation added');
  }

  async function deleteAnnotation(annotationId: string): Promise<void> {
    await run(async () => {
      await client.deleteAnnotation(work.id, annotationId);
      annotations = await client.listAnnotations(work.id);
    }, 'Annotation deleted');
  }

  function clearReader(): void {
    if (readerUrl) URL.revokeObjectURL(readerUrl);
    readerUrl = null;
    readerFile = null;
    readerJumpReferenceId = null;
  }

  onDestroy(clearReader);
</script>

<div class="detail">
  <div class="bar">
    <h2>{form.canonical_title || 'Untitled paper'}</h2>
    <div class="bar-actions">
      <button type="button" class="secondary small" on:click={exportNotes} disabled={loading}
        title="Download this paper's annotations as Markdown">Export notes</button>
      <button type="button" class="secondary small danger-btn" on:click={deletePaper} disabled={loading || !$canEdit}
        title={$canEdit ? 'Delete this paper (files are kept)' : INSUFFICIENT_ROLE}>Delete</button>
      <button type="button" class="secondary small" on:click={onClose} title="Close detail panel">✕</button>
    </div>
  </div>
  {#if message}<p class="muted">{message}</p>{/if}
  {#if work.keywords && work.keywords.length}
    <div class="keywords">
      {#each work.keywords as kw}<span class="kw">{kw}</span>{/each}
    </div>
  {/if}

  <details open>
    <summary>Details</summary>
    <form class="fields" on:submit|preventDefault={save}>
      <label>Title<input bind:value={form.canonical_title} /></label>
      <div class="two">
        <label>Year<input bind:value={form.year} inputmode="numeric" /></label>
        <label>Reading status
          <select bind:value={form.reading_status}>
            {#each STATUSES as s}<option value={s}>{s}</option>{/each}
          </select>
        </label>
      </div>
      <label>Venue<input bind:value={form.venue} /></label>
      <div class="two">
        <label>DOI<input bind:value={form.doi} placeholder="10.xxxx/…" /></label>
        <label>arXiv id<input bind:value={form.arxiv_id} placeholder="1706.03762" /></label>
      </div>
      <label>Abstract<textarea bind:value={form.abstract} rows="4"></textarea></label>
      <div class="actions">
        <button type="submit" disabled={loading || !$canEdit}
          title={$canEdit ? '' : INSUFFICIENT_ROLE}>Save changes</button>
        <button type="button" class="secondary" on:click={enrich} disabled={loading || !$canEdit || (!form.doi && !form.arxiv_id)}
          title={!$canEdit
            ? INSUFFICIENT_ROLE
            : form.doi || form.arxiv_id
              ? 'Fetch external metadata'
              : 'Needs a DOI or arXiv id to enrich'}>Enrich</button>
      </div>
      {#if $canEdit && !form.doi && !form.arxiv_id}<p class="hintline">Add a DOI or arXiv id to enable “Enrich”.</p>{/if}
      {#if !$canEdit}<p class="hintline">{INSUFFICIENT_ROLE} — your role is read-only for library content.</p>{/if}
    </form>
  </details>

  <details>
    <summary>Metadata review {#if fields.some((f) => f.has_conflict)}<span class="conflict">conflicts</span>{/if}</summary>
    {#if fields.length === 0}
      <p class="empty">No metadata assertions yet. Enrich or extract to gather them.</p>
    {:else}
      <div class="reviews">
        {#each fields as field (field.field_name)}
          <div class="review" class:has-conflict={field.has_conflict}>
            <strong>{field.field_name}</strong>
            <button
              type="button"
              class="lock"
              class:locked={field.confirmed}
              on:click={() => toggleLock(field.field_name, !field.confirmed)}
              disabled={loading || !$canEdit}
              title={!$canEdit
                ? INSUFFICIENT_ROLE
                : field.confirmed
                  ? 'Locked — enrichment will not overwrite this field. Click to unlock.'
                  : 'Unlocked — click to lock so enrichment cannot overwrite it.'}
            >{field.confirmed ? '🔒 locked' : '🔓 lock'}</button>
            {#each field.assertions as a (a.id)}
              <div class="assertion">
                <span class="src">{a.source}</span>
                <span class="val">{a.value}</span>
                {#if a.selected_as_canonical}
                  <span class="canon">canonical</span>
                {:else}
                  <button type="button" class="secondary small" on:click={() => selectCanonical(a.id)} disabled={loading || !$canEdit}
                    title={$canEdit ? 'Use this value as the canonical one' : INSUFFICIENT_ROLE}>Use this</button>
                {/if}
              </div>
            {/each}
          </div>
        {/each}
      </div>
    {/if}
  </details>

  <details>
    <summary>Files ({files.length})</summary>
    <div class="attach">
      <input type="file" accept=".pdf,application/pdf" on:change={(e) => (attachFile = e.currentTarget.files?.[0] ?? null)} aria-label="Attach PDF" />
      <button type="button" on:click={upload} disabled={!attachFile || loading || !$canEdit}
        title={!$canEdit ? INSUFFICIENT_ROLE : attachFile ? 'Attach this PDF to the paper' : 'Choose a PDF to attach'}>Attach PDF</button>
    </div>
    {#if files.length === 0}
      <p class="empty">No files attached. Attach a PDF above to read and extract it.</p>
    {:else}
      <ul class="files">
        {#each files as file (file.id)}
          <li class="entry-card" class:unavailable={!file.content_available}>
            <div class="file-row">
              <div class="file-main">
                <span class="fname">{file.original_filename ?? file.id.slice(0, 8)}</span>
                <small class="muted">{formatBytes(file.size_bytes)}</small>
                <span class="fstatus fstatus-{file.status}">{fileStatusLabel(file.status)}</span>
                {#if !file.content_available}
                  <span class="fstatus funavail" title="The PDF bytes are not stored on the server (extracted-only or the source location was removed).">file unavailable</span>
                {/if}
              </div>
              <span class="file-actions">
                <button type="button" class="secondary small" on:click={() => openInReader(file)}
                  disabled={loading || !file.content_available}
                  title={file.content_available
                    ? 'Open in the in-app reader (annotations + citation overlay)'
                    : 'PDF not available on the server — it was extracted-only or its source was removed.'}>Read</button>
                <button type="button" class="secondary small" on:click={() => openInNewTab(file)}
                  disabled={loading || !file.content_available}
                  title={file.content_available
                    ? 'Open the raw PDF in a new browser tab'
                    : 'PDF not available on the server.'}>New tab ↗</button>
                <button type="button" class="secondary small" on:click={() => reextract(file)} disabled={loading || !$canEdit}
                  title={$canEdit ? 'Queue GROBID extraction again for this file' : INSUFFICIENT_ROLE}>Re-extract</button>
              </span>
            </div>
            <button
              type="button"
              class="hash"
              on:click={() => copyHash(file.sha256)}
              title="Content hash (SHA-256) — matches the agent's local file id. Click to copy; searchable in the Library search box."
            >{file.sha256}</button>
          </li>
        {/each}
      </ul>
      <p class="hintline">
        The <strong>hash</strong> is the file's content hash — the same value the agent shows as its
        local file id. Click to copy, or paste it (or a prefix) into the Library search box to find
        the owning paper.
      </p>
    {/if}
  </details>

  <details on:toggle={(e) => e.currentTarget.open && !relatedLoaded && loadRelated()}>
    <summary>Related papers</summary>
    {#if !relatedLoaded}
      <p class="hintline">Open to find papers similar to this one (by embedding neighborhood).</p>
    {:else if related.length === 0}
      <p class="empty">No related papers found (build embeddings via Admin → AI &amp; Models → Reindex).</p>
    {:else}
      <ul class="refs">
        {#each related as r (r.id)}
          <li><span class="ref-title">{r.canonical_title ?? 'Untitled'}</span>
            <small class="muted">{r.year ?? ''}</small></li>
        {/each}
      </ul>
    {/if}
  </details>

  <details>
    <summary>Tags</summary>
    <div class="tags">
      <select bind:value={applyTagId} aria-label="Tag">
        <option value="">Choose a tag…</option>
        {#each tags as tag (tag.id)}<option value={tag.id}>{tag.name}</option>{/each}
      </select>
      <button type="button" class="secondary" on:click={applyTag} disabled={!applyTagId || loading || !$canEdit}
        title={$canEdit ? '' : INSUFFICIENT_ROLE}>Apply</button>
      <button type="button" class="secondary" on:click={removeTag} disabled={!applyTagId || loading || !$canEdit}
        title={$canEdit ? '' : INSUFFICIENT_ROLE}>Remove</button>
    </div>
    <p class="hintline">Create tags on the Tags tab. (Currently-applied tags aren't listed yet.)</p>
  </details>

  <details class="references-block" open={references.length > 0}>
    <summary>References ({references.length})</summary>
    {#if references.length === 0}
      <p class="empty">
        No references extracted yet. They appear after GROBID extraction runs on an attached PDF
        (watch the Jobs tab); a manually-created paper with no PDF won’t have any.
      </p>
    {:else}
      <ol class="refs">
        {#each references as ref (ref.id)}
          <li class="entry-card" id={`ref-${ref.id}`} class:flash-ref={flashRefId === ref.id}>
            <div class="ref-head">
              {#if ref.shorthand}<span class="ref-marker" title="In-text citation marker for this reference">{ref.shorthand}</span>{/if}
              <span class="ref-title">{ref.title ?? ref.raw_citation ?? 'Untitled reference'}</span>
            </div>
            <small class="muted">
              {ref.year ?? ''}{ref.doi ? ` · doi:${ref.doi}` : ''}{ref.arxiv_id
                ? ` · arXiv:${ref.arxiv_id}`
                : ''}
              {#if ref.resolved_work_id}<span class="ref-badge">in library</span>{/if}
            </small>
            <div class="ref-actions">
              {#if locatableReferenceIds.has(ref.id)}
                <button type="button" class="secondary small" on:click={() => findReferenceInText(ref.id)}
                  title="Open the reader and jump to where this reference is cited">Find in text</button>
              {/if}
              {#if !ref.resolved_work_id}
                <button type="button" class="secondary small" on:click={() => importReference(ref.id)}
                  disabled={loading || !$canEdit}
                  title={$canEdit ? 'Create a library paper from this reference' : INSUFFICIENT_ROLE}>Import</button>
              {/if}
            </div>
          </li>
        {/each}
      </ol>
    {/if}
  </details>

  {#if contexts.length}
    <details>
      <summary>In-text citations ({contexts.length})</summary>
      <p class="hintline">Open this paper with “Read”, then click a citation highlight to reveal its reference (or use a reference’s “Find in text”).</p>
      <ul class="ctx">
        {#each contexts as c (c.id)}
          <li class="entry-card">
            <strong class="ref-marker">{c.marker_text ?? '•'}</strong>
            <span>{c.context_sentence ?? c.reference_title ?? c.reference_raw_citation ?? ''}</span>
          </li>
        {/each}
      </ul>
    </details>
  {/if}

  {#if summaries.length}
    <details>
      <summary>Summaries ({summaries.length})</summary>
      {#each summaries as s (s.id)}<p class="muted">{s.summary_type}: {s.text}</p>{/each}
    </details>
  {/if}
</div>

{#if showReader && readerUrl}
  <Modal title={readerFile?.original_filename ?? 'PDF reader'} wide onClose={closeReader}>
    <PdfReader
      fileId={readerFile?.id ?? ''}
      fileName={readerFile?.original_filename ?? 'PDF'}
      fileUrl={readerUrl}
      {contexts}
      {annotations}
      onCreateAnnotation={createAnnotation}
      onDeleteAnnotation={deleteAnnotation}
      onNavigateToReference={navigateToReference}
      initialJumpReferenceId={readerJumpReferenceId}
    />
  </Modal>
{/if}

<style>
  .detail {
    display: grid;
    gap: 0.6rem;
  }

  .bar {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0;
    overflow-wrap: anywhere;
  }


  details {
    background: #f4f6f9;
    border: 1px solid #e1e7ee;
    border-radius: 6px;
    padding: 0.5rem 0.7rem;
  }

  summary {
    cursor: pointer;
    font-weight: 700;
  }

  .fields {
    display: grid;
    gap: 0.5rem;
    margin-top: 0.6rem;
  }

  .two {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: 1fr 1fr;
  }

  .actions {
    display: flex;
    gap: 0.5rem;
  }

  .reviews {
    display: grid;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }

  .review {
    background: white;
    border: 1px solid #e1e7ee;
    border-radius: 6px;
    padding: 0.5rem;
  }

  .review.has-conflict {
    border-color: #f0b429;
  }

  .assertion {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    margin-top: 0.3rem;
  }

  .src {
    background: #e2e8f0;
    border-radius: 0.25rem;
    font-size: 0.72rem;
    padding: 0.05rem 0.35rem;
  }

  .val {
    flex: 1;
    overflow-wrap: anywhere;
  }

  .canon {
    color: #14532d;
    font-size: 0.72rem;
    font-weight: 700;
  }

  .conflict {
    background: #fde68a;
    border-radius: 0.25rem;
    color: #78350f;
    font-size: 0.72rem;
    margin-left: 0.4rem;
    padding: 0.05rem 0.35rem;
  }

  .attach,
  .tags {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }

  /* Rounded-rect card around each References / In-text-citation / File entry so it is
     unambiguous which Import button / text / hash belongs to which entry. */
  .entry-card {
    background: #fff;
    border: 1px solid #e1e7ee;
    border-radius: 8px;
    padding: 0.5rem 0.6rem;
  }

  .files {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
  }

  .files li {
    display: grid;
    gap: 0.4rem;
  }

  .file-row {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    justify-content: space-between;
  }

  .files li.unavailable {
    background: #fbfbfc;
    border-style: dashed;
  }

  .file-main {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    min-width: 0;
  }

  .fname {
    overflow-wrap: anywhere;
  }

  .fstatus {
    border-radius: 0.25rem;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 0.05rem 0.35rem;
    text-transform: uppercase;
  }

  .fstatus-extracted {
    background: #bbf7d0;
    color: #14532d;
  }

  .fstatus-extract_failed {
    background: #fecaca;
    color: #7f1d1d;
  }

  .fstatus-available {
    background: #e2e8f0;
    color: #475569;
  }

  .fstatus-extracted_discarded {
    background: #e2e8f0;
    color: #475569;
  }

  .funavail {
    background: #fed7aa;
    color: #7c2d12;
  }

  .hash {
    background: #eef1f4;
    border: 1px solid #d8dee6;
    border-radius: 0.25rem;
    color: #475569;
    cursor: pointer;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.72rem;
    min-height: auto;
    /* Full hash, selectable (browser Ctrl+F finds it) and wrapping so the row never scrolls. */
    overflow-wrap: anywhere;
    padding: 0.15rem 0.4rem;
    text-align: left;
    user-select: text;
    width: 100%;
  }

  .small {
    min-height: 1.9rem;
    padding: 0.2rem 0.5rem;
  }

  .file-actions {
    display: flex;
    flex-shrink: 0;
    gap: 0.35rem;
  }

  .bar-actions {
    display: flex;
    gap: 0.35rem;
  }

  .danger-btn {
    border-color: #f1b0a8;
    color: #b3261e;
  }

  .keywords {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .kw {
    background: #eef1f4;
    border-radius: 10px;
    color: #41505f;
    font-size: 0.72rem;
    padding: 0.05rem 0.45rem;
  }

  .lock {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 0.72rem;
    margin-left: 0.4rem;
    padding: 0;
  }

  .lock.locked {
    color: #14532d;
    font-weight: 700;
  }

  .refs {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    margin: 0.5rem 0 0;
    padding-left: 1.1rem;
  }

  .refs li {
    display: grid;
    gap: 0.1rem;
  }

  .ref-head {
    align-items: baseline;
    display: flex;
    gap: 0.4rem;
  }

  .ref-marker {
    background: #dbeafe;
    border-radius: 0.25rem;
    color: #1e3a8a;
    flex-shrink: 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.72rem;
    font-weight: 700;
    padding: 0.05rem 0.4rem;
    white-space: nowrap;
  }

  .ref-title {
    overflow-wrap: anywhere;
  }

  .ref-badge {
    background: #bbf7d0;
    border-radius: 0.25rem;
    color: #14532d;
    font-size: 0.68rem;
    margin-left: 0.3rem;
    padding: 0.03rem 0.3rem;
  }

  .ref-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.25rem;
  }

  .flash-ref {
    animation: flash-ref 0.6s ease-in-out 2;
  }

  @keyframes flash-ref {
    50% {
      background: rgba(255, 200, 90, 0.6);
    }
  }

  .ctx {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    list-style: none;
    margin: 0.4rem 0 0;
    max-height: 16rem;
    overflow: auto;
    padding: 0;
  }

  .ctx li {
    display: flex;
    gap: 0.4rem;
  }

  .ctx span {
    overflow-wrap: anywhere;
  }
</style>
