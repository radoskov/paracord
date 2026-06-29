<script lang="ts">
  import { onDestroy } from 'svelte';

  import type { Annotation, CitationContext, PdfCoordinateBox } from '../api/client';

  export let fileId: string;
  export let fileName: string;
  export let fileUrl: string | null = null;
  export let contexts: CitationContext[] = [];
  export let annotations: Annotation[] = [];
  export let onCreateAnnotation:
    | ((payload: {
        annotation_type: string;
        page: number | null;
        selected_text: string | null;
        content_markdown: string | null;
        coordinates: Record<string, unknown> | null;
      }) => Promise<void>)
    | null = null;

  type PdfModule = typeof import('pdfjs-dist');

  let tab: 'pdf' | 'contexts' | 'annotations' = 'pdf';

  // --- PDF.js state -------------------------------------------------------
  let pdfjs: PdfModule | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let pdfDoc: any = null;
  let loadedUrl: string | null = null;
  let numPages = 0;
  let currentPage = 1;
  let scale = 1.3;
  let pageWidth = 0;
  let pageHeight = 0;
  let loadingPdf = false;
  let pdfError = '';
  let canvasEl: HTMLCanvasElement | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let renderTask: any = null;

  // --- search -------------------------------------------------------------
  let searchQuery = '';
  let searchHits: number[] = [];
  let searchPos = -1;
  let searching = false;

  // --- selection → annotation --------------------------------------------
  let flashBoxKey = '';

  // --- annotation form ----------------------------------------------------
  let annotationType = 'note';
  let annotationPage = '';
  let selectedText = '';
  let annotationContent = '';
  let selectionCoords: PdfCoordinateBox | null = null;

  // Citation boxes whose primary page is the page currently shown.
  $: pageBoxes = contexts
    .filter((c) => (c.pdf_coordinates?.length ?? 0) > 0)
    .flatMap((c) =>
      (c.pdf_coordinates ?? [])
        .filter((box) => box.page === currentPage)
        .map((box) => ({ box, context: c })),
    );

  async function ensurePdfjs(): Promise<PdfModule> {
    if (pdfjs) return pdfjs;
    const mod = (await import('pdfjs-dist')) as PdfModule;
    // The worker is bundled by Vite via the ?url suffix; set once.
    const workerUrl = (await import('pdfjs-dist/build/pdf.worker.min.mjs?url')).default;
    mod.GlobalWorkerOptions.workerSrc = workerUrl;
    pdfjs = mod;
    return mod;
  }

  async function loadPdf(url: string): Promise<void> {
    loadingPdf = true;
    pdfError = '';
    try {
      const lib = await ensurePdfjs();
      const buffer = await (await fetch(url)).arrayBuffer();
      const doc = await lib.getDocument({ data: buffer }).promise;
      if (pdfDoc) await pdfDoc.destroy().catch(() => undefined);
      pdfDoc = doc;
      loadedUrl = url;
      numPages = doc.numPages;
      currentPage = 1;
      searchHits = [];
      searchPos = -1;
      await renderPage(1);
    } catch (error) {
      pdfError = error instanceof Error ? error.message : 'Could not render PDF';
    } finally {
      loadingPdf = false;
    }
  }

  async function renderPage(n: number): Promise<void> {
    if (!pdfDoc || !canvasEl) return;
    currentPage = Math.min(Math.max(1, n), numPages);
    if (renderTask) {
      renderTask.cancel();
      renderTask = null;
    }
    const page = await pdfDoc.getPage(currentPage);
    const viewport = page.getViewport({ scale });
    pageWidth = viewport.width;
    pageHeight = viewport.height;
    const canvas = canvasEl;
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    renderTask = page.render({ canvasContext: ctx, viewport });
    try {
      await renderTask.promise;
    } catch {
      // Render cancelled by a newer navigation — ignore.
    } finally {
      renderTask = null;
    }
  }

  function goTo(n: number): void {
    void renderPage(n);
  }

  function zoom(delta: number): void {
    scale = Math.min(3, Math.max(0.5, Math.round((scale + delta) * 10) / 10));
    void renderPage(currentPage);
  }

  async function runSearch(): Promise<void> {
    const query = searchQuery.trim().toLowerCase();
    if (!query || !pdfDoc) {
      searchHits = [];
      searchPos = -1;
      return;
    }
    searching = true;
    try {
      const hits: number[] = [];
      for (let p = 1; p <= numPages; p += 1) {
        const page = await pdfDoc.getPage(p);
        const content = await page.getTextContent();
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const text = content.items.map((item: any) => item.str ?? '').join(' ');
        if (text.toLowerCase().includes(query)) hits.push(p);
      }
      searchHits = hits;
      searchPos = hits.length ? 0 : -1;
      if (hits.length) goTo(hits[0]);
    } finally {
      searching = false;
    }
  }

  function stepSearch(delta: number): void {
    if (!searchHits.length) return;
    searchPos = (searchPos + delta + searchHits.length) % searchHits.length;
    goTo(searchHits[searchPos]);
  }

  function jumpToContext(context: CitationContext): void {
    const box = context.pdf_coordinates?.[0];
    tab = 'pdf';
    if (box) {
      flashBoxKey = `${context.id}`;
      goTo(box.page);
      window.setTimeout(() => (flashBoxKey = ''), 2000);
    } else if (context.page) {
      goTo(context.page);
    }
  }

  function captureSelection(): void {
    const selection = window.getSelection();
    const text = selection?.toString().trim() ?? '';
    if (!text) return;
    selectedText = text;
    annotationPage = String(currentPage);
    annotationType = 'highlight';
    tab = 'annotations';
    // Best-effort bounding box of the selection relative to the rendered page, in PDF
    // points (canvas px / scale) so it round-trips with stored GROBID-style coordinates.
    const rect = selection?.rangeCount ? selection.getRangeAt(0).getBoundingClientRect() : null;
    const canvasRect = canvasEl?.getBoundingClientRect();
    if (rect && canvasRect && rect.width) {
      selectionCoords = {
        page: currentPage,
        x: Math.round(((rect.left - canvasRect.left) / scale) * 100) / 100,
        y: Math.round(((rect.top - canvasRect.top) / scale) * 100) / 100,
        w: Math.round((rect.width / scale) * 100) / 100,
        h: Math.round((rect.height / scale) * 100) / 100,
      };
    } else {
      selectionCoords = null;
    }
  }

  async function createAnnotation(): Promise<void> {
    if (!onCreateAnnotation) return;
    await onCreateAnnotation({
      annotation_type: annotationType,
      page: annotationPage ? Number(annotationPage) : null,
      selected_text: selectedText || null,
      content_markdown: annotationContent || null,
      coordinates: selectionCoords ? { boxes: [selectionCoords] } : null,
    });
    selectedText = '';
    annotationContent = '';
    selectionCoords = null;
  }

  // Load (or reload) whenever a new PDF URL is shown in the PDF tab.
  $: if (tab === 'pdf' && fileUrl && fileUrl !== loadedUrl && !loadingPdf) {
    void loadPdf(fileUrl);
  }
  // Re-render after the canvas element mounts for an already-loaded doc.
  $: if (tab === 'pdf' && canvasEl && pdfDoc && loadedUrl === fileUrl) {
    void renderPage(currentPage);
  }

  onDestroy(() => {
    if (renderTask) renderTask.cancel();
    if (pdfDoc) void pdfDoc.destroy().catch(() => undefined);
  });
</script>

<section class="reader">
  <header>
    <div>
      <h3>{fileName}</h3>
      <span>{fileId.slice(0, 8)}</span>
    </div>
    <nav aria-label="Reader panels">
      <button type="button" class:active={tab === 'pdf'} on:click={() => (tab = 'pdf')}>PDF</button>
      <button type="button" class:active={tab === 'contexts'} on:click={() => (tab = 'contexts')}>
        References
      </button>
      <button
        type="button"
        class:active={tab === 'annotations'}
        on:click={() => (tab = 'annotations')}
      >
        Notes
      </button>
    </nav>
  </header>

  {#if tab === 'pdf'}
    {#if !fileUrl}
      <p class="empty">Open a PDF in the reader</p>
    {:else}
      <div class="pdf-toolbar">
        <div class="pager">
          <button type="button" on:click={() => goTo(currentPage - 1)} disabled={currentPage <= 1}>
            ‹
          </button>
          <span>{currentPage} / {numPages || '?'}</span>
          <button
            type="button"
            on:click={() => goTo(currentPage + 1)}
            disabled={currentPage >= numPages}
          >
            ›
          </button>
        </div>
        <div class="zoom">
          <button type="button" on:click={() => zoom(-0.2)} aria-label="Zoom out">−</button>
          <span>{Math.round(scale * 100)}%</span>
          <button type="button" on:click={() => zoom(0.2)} aria-label="Zoom in">+</button>
        </div>
        <form class="search" on:submit|preventDefault={runSearch}>
          <input bind:value={searchQuery} placeholder="Search text…" />
          <button type="submit" disabled={searching || !searchQuery.trim()}>Find</button>
          {#if searchHits.length}
            <button type="button" on:click={() => stepSearch(-1)} aria-label="Previous match"
              >‹</button
            >
            <span>{searchPos + 1}/{searchHits.length}</span>
            <button type="button" on:click={() => stepSearch(1)} aria-label="Next match">›</button>
          {/if}
        </form>
        <button type="button" class="select-btn" on:click={captureSelection}>
          Highlight selection
        </button>
      </div>

      {#if pdfError}
        <p class="error">{pdfError}</p>
      {/if}

      <div class="pdf-body">
        <div class="thumbs" aria-label="Page thumbnails">
          {#each Array(numPages) as _, i (i)}
            <button
              type="button"
              class:active={currentPage === i + 1}
              on:click={() => goTo(i + 1)}
            >
              {i + 1}
            </button>
          {/each}
        </div>
        <div class="page-wrap">
          {#if loadingPdf}<p class="empty">Rendering…</p>{/if}
          <div class="canvas-stage" style={`width:${pageWidth}px;height:${pageHeight}px`}>
            <canvas bind:this={canvasEl}></canvas>
            {#each pageBoxes as item (item.context.id + ':' + item.box.x + ',' + item.box.y)}
              <button
                type="button"
                class="overlay"
                class:flash={flashBoxKey === item.context.id}
                title={item.context.reference_title ??
                  item.context.marker_text ??
                  'citation'}
                on:click={() => (tab = 'contexts')}
                style={`left:${item.box.x * scale}px;top:${item.box.y * scale}px;width:${
                  item.box.w * scale
                }px;height:${item.box.h * scale}px`}
              ></button>
            {/each}
          </div>
        </div>
      </div>
    {/if}
  {:else if tab === 'contexts'}
    {#if contexts.length === 0}
      <p class="empty">No citation contexts extracted</p>
    {:else}
      <div class="context-list">
        {#each contexts as context}
          <article>
            <header>
              <strong>{context.marker_text ?? 'citation'}</strong>
              <span>{context.section_label ?? 'section unknown'}</span>
            </header>
            <p>{context.context_sentence ?? 'No sentence context'}</p>
            <small>
              {context.reference_title ?? context.reference_raw_citation ?? 'Unparsed reference'}
            </small>
            {#if (context.pdf_coordinates?.length ?? 0) > 0 || context.page}
              <button type="button" class="jump" on:click={() => jumpToContext(context)}>
                Jump to p.{context.pdf_coordinates?.[0]?.page ?? context.page}
              </button>
            {/if}
          </article>
        {/each}
      </div>
    {/if}
  {:else}
    <form class="annotation-form" on:submit|preventDefault={createAnnotation}>
      <select bind:value={annotationType} disabled={!onCreateAnnotation}>
        <option value="note">Note</option>
        <option value="highlight">Highlight</option>
        <option value="page_anchor">Page anchor</option>
        <option value="citation_note">Citation note</option>
      </select>
      <input bind:value={annotationPage} inputmode="numeric" placeholder="Page" />
      <input bind:value={selectedText} placeholder="Selected text" />
      <textarea bind:value={annotationContent} placeholder="Note"></textarea>
      {#if selectionCoords}
        <small class="coord-note">Anchored at p.{selectionCoords.page} (from selection)</small>
      {/if}
      <button type="submit" disabled={!onCreateAnnotation || (!selectedText && !annotationContent)}>
        Add
      </button>
    </form>

    {#if annotations.length === 0}
      <p class="empty">No annotations</p>
    {:else}
      <div class="annotation-list">
        {#each annotations as annotation}
          <article>
            <header>
              <strong>{annotation.annotation_type.replaceAll('_', ' ')}</strong>
              <span>page {annotation.page ?? '-'}</span>
            </header>
            {#if annotation.selected_text}<p>{annotation.selected_text}</p>{/if}
            {#if annotation.content_markdown}<small>{annotation.content_markdown}</small>{/if}
          </article>
        {/each}
      </div>
    {/if}
  {/if}
</section>

<style>
  .reader {
    display: grid;
    gap: 0.7rem;
  }

  header {
    align-items: center;
    display: flex;
    gap: 0.75rem;
    justify-content: space-between;
  }

  header div {
    display: grid;
    gap: 0.2rem;
    min-width: 0;
  }

  h3 {
    color: #1f2a36;
    font-size: 1rem;
    line-height: 1.2;
    margin: 0;
    overflow-wrap: anywhere;
  }

  span,
  small,
  .empty {
    color: #667381;
  }

  span {
    font-size: 0.78rem;
  }

  nav {
    display: flex;
    gap: 0.35rem;
  }

  button {
    background: white;
    border: 1px solid #bcc7d2;
    border-radius: 6px;
    color: #21303d;
    cursor: pointer;
    font: inherit;
    font-weight: 700;
    min-height: 2rem;
    padding: 0.3rem 0.55rem;
  }

  button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  input,
  select,
  textarea {
    border: 1px solid #bcc7d2;
    border-radius: 6px;
    font: inherit;
    min-height: 2.2rem;
    padding: 0.35rem 0.5rem;
  }

  textarea {
    min-height: 4.5rem;
    resize: vertical;
  }

  button.active {
    background: #203142;
    color: white;
  }

  .pdf-toolbar {
    align-items: center;
    background: #f4f6f9;
    border: 1px solid #d8dee6;
    border-radius: 6px;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 0.75rem;
    padding: 0.45rem 0.6rem;
  }

  .pager,
  .zoom,
  .search {
    align-items: center;
    display: flex;
    gap: 0.35rem;
  }

  .search {
    flex: 1;
    min-width: 12rem;
  }

  .search input {
    flex: 1;
    min-width: 0;
  }

  .select-btn {
    margin-left: auto;
  }

  .pdf-body {
    display: grid;
    gap: 0.6rem;
    grid-template-columns: 3.5rem minmax(0, 1fr);
  }

  .thumbs {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    max-height: min(72vh, 48rem);
    overflow: auto;
  }

  .thumbs button {
    min-height: 2rem;
    padding: 0.25rem;
  }

  .page-wrap {
    background: #eef2f6;
    border: 1px solid #d8dee6;
    border-radius: 6px;
    display: flex;
    justify-content: center;
    max-height: min(72vh, 48rem);
    overflow: auto;
    padding: 0.6rem;
  }

  .canvas-stage {
    position: relative;
  }

  .canvas-stage canvas {
    display: block;
  }

  .overlay {
    background: rgba(255, 214, 0, 0.28);
    border: 1px solid rgba(204, 150, 0, 0.7);
    border-radius: 2px;
    cursor: pointer;
    min-height: 0;
    padding: 0;
    position: absolute;
  }

  .overlay.flash {
    animation: flash 0.6s ease-in-out 2;
    background: rgba(255, 138, 0, 0.5);
  }

  @keyframes flash {
    50% {
      background: rgba(255, 90, 0, 0.7);
    }
  }

  .jump {
    margin-top: 0.4rem;
    min-height: 1.8rem;
    padding: 0.2rem 0.5rem;
  }

  .error {
    color: #b3261e;
  }

  .coord-note {
    grid-column: 1 / -1;
  }

  .annotation-form {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(8rem, 10rem) minmax(5rem, 7rem) minmax(0, 1fr) auto;
  }

  .annotation-form textarea {
    grid-column: 1 / -1;
  }

  .context-list,
  .annotation-list {
    display: grid;
    gap: 0.65rem;
    max-height: min(72vh, 48rem);
    overflow: auto;
  }

  .context-list article,
  .annotation-list article {
    background: #eef2f6;
    border: 1px solid #d8dee6;
    border-radius: 6px;
    padding: 0.7rem;
  }

  .context-list article header,
  .annotation-list article header {
    margin-bottom: 0.35rem;
  }

  .context-list p,
  .context-list small,
  .annotation-list p,
  .annotation-list small {
    overflow-wrap: anywhere;
  }

  .context-list p,
  .annotation-list p {
    margin: 0 0 0.35rem;
  }

  @media (max-width: 760px) {
    .annotation-form {
      grid-template-columns: 1fr;
    }

    .pdf-body {
      grid-template-columns: 1fr;
    }

    .thumbs {
      flex-direction: row;
      max-height: none;
    }
  }
</style>
