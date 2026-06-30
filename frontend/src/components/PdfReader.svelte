<script lang="ts">
  import { onDestroy, tick } from 'svelte';

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
  export let onDeleteAnnotation: ((annotationId: string) => Promise<void>) | null = null;
  // Switch tab + scroll/flash the matching reference entry in the parent detail view.
  export let onNavigateToReference: ((referenceId: string) => void) | null = null;
  // When set, the reader opens and jumps to the first in-text mention of this reference.
  export let initialJumpReferenceId: string | null = null;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type PdfModule = any;

  let tab: 'pdf' | 'contexts' | 'annotations' = 'pdf';

  // --- PDF.js state -------------------------------------------------------
  let pdfjs: PdfModule | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let TextLayerCtor: any = null;
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
  let textLayerEl: HTMLDivElement | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let renderTask: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let currentTextLayer: any = null;

  // --- view mode (paged ↔ smooth scroll) ----------------------------------
  const VIEW_MODE_KEY = 'paracord.reader.viewMode';
  type ViewMode = 'paged' | 'scroll';
  function readStoredViewMode(): ViewMode {
    try {
      return localStorage.getItem(VIEW_MODE_KEY) === 'scroll' ? 'scroll' : 'paged';
    } catch {
      return 'paged';
    }
  }
  let viewMode: ViewMode = readStoredViewMode();
  function setViewMode(mode: ViewMode): void {
    if (mode === viewMode) return;
    viewMode = mode;
    try {
      localStorage.setItem(VIEW_MODE_KEY, mode);
    } catch {
      // localStorage may be unavailable (private mode) — keep the in-memory choice.
    }
  }

  // --- search -------------------------------------------------------------
  let searchQuery = '';
  // One entry per occurrence across the whole document.
  type SearchHit = { page: number; index: number };
  let searchHits: SearchHit[] = [];
  let searchPos = -1;
  let searching = false;
  // Lazily-built per-page text cache: lowercased page text for whole-doc scan.
  const pageTextCache = new Map<number, string>();

  // --- selection → annotation --------------------------------------------
  // Generalised flash key, shared by citation overlays, annotation boxes and references.
  let flashKey = '';
  function flash(key: string): void {
    flashKey = key;
    window.setTimeout(() => {
      if (flashKey === key) flashKey = '';
    }, 2000);
  }

  // --- annotation form ----------------------------------------------------
  let annotationType = 'note';
  let annotationPage = '';
  let selectedText = '';
  let annotationContent = '';
  let selectionBoxes: PdfCoordinateBox[] | null = null;

  // Boxes are stored in PDF user-space, top-left origin (matching GROBID), so a shared
  // converter maps them back to on-screen pixels for the current scale.
  function boxToStyle(box: PdfCoordinateBox): string {
    return (
      `left:${box.x * scale}px;top:${box.y * scale}px;` +
      `width:${box.w * scale}px;height:${box.h * scale}px`
    );
  }

  // Citation boxes whose page is the page currently shown.
  $: pageBoxes = contexts
    .filter((c) => (c.pdf_coordinates?.length ?? 0) > 0)
    .flatMap((c) =>
      (c.pdf_coordinates ?? [])
        .filter((box) => box.page === currentPage)
        .map((box) => ({ box, context: c })),
    );

  // Persisted annotation highlight boxes on the current page.
  $: annotationBoxes = annotations.flatMap((a) => {
    const boxes = (a.coordinates as { boxes?: PdfCoordinateBox[] } | null)?.boxes ?? [];
    return boxes
      .filter((box) => box.page === currentPage)
      .map((box) => ({ box, annotation: a }));
  });

  async function ensurePdfjs(): Promise<PdfModule> {
    if (pdfjs) return pdfjs;
    const mod = (await import('pdfjs-dist')) as PdfModule;
    // The worker is bundled by Vite via the ?url suffix; set once.
    const workerUrl = (await import('pdfjs-dist/build/pdf.worker.min.mjs?url')).default;
    mod.GlobalWorkerOptions.workerSrc = workerUrl;
    TextLayerCtor = mod.TextLayer;
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
      pageTextCache.clear();
      await renderPage(1);
      if (initialJumpReferenceId) void jumpToReferenceMention(initialJumpReferenceId);
    } catch (error) {
      pdfError = error instanceof Error ? error.message : 'Could not render PDF';
    } finally {
      loadingPdf = false;
    }
  }

  // Render a page's canvas + text layer into the given elements. Shared by the paged view,
  // the smooth-scroll view and search. Returns the viewport so callers can map coordinates.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async function renderPageInto(
    n: number,
    canvas: HTMLCanvasElement,
    layer: HTMLDivElement | null,
    s: number,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ): Promise<{ viewport: any; textLayer: any } | null> {
    if (!pdfDoc || !TextLayerCtor) return null;
    const page = await pdfDoc.getPage(n);
    const viewport = page.getViewport({ scale: s });
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const task = page.render({ canvasContext: ctx, viewport });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let textLayer: any = null;
    try {
      await task.promise;
      if (layer) {
        layer.replaceChildren();
        const content = await page.getTextContent();
        textLayer = new TextLayerCtor({ textContentSource: content, container: layer, viewport });
        await textLayer.render();
      }
    } catch {
      // Render cancelled by a newer navigation — ignore.
    }
    return { viewport, textLayer };
  }

  async function renderPage(n: number): Promise<void> {
    if (!pdfDoc || !canvasEl) return;
    currentPage = Math.min(Math.max(1, n), numPages);
    if (renderTask) {
      renderTask.cancel();
      renderTask = null;
    }
    if (currentTextLayer) {
      try {
        currentTextLayer.cancel();
      } catch {
        // Already settled.
      }
      currentTextLayer = null;
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
      if (textLayerEl) {
        textLayerEl.replaceChildren();
        const content = await page.getTextContent();
        currentTextLayer = new TextLayerCtor({
          textContentSource: content,
          container: textLayerEl,
          viewport,
        });
        await currentTextLayer.render();
        applySearchHighlights();
      }
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
    if (viewMode === 'scroll') resetScrollRender();
    else void renderPage(currentPage);
  }

  // --- whole-document search ----------------------------------------------
  async function pageText(p: number): Promise<string> {
    const cached = pageTextCache.get(p);
    if (cached !== undefined) return cached;
    const page = await pdfDoc.getPage(p);
    const content = await page.getTextContent();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const text = content.items.map((item: any) => item.str ?? '').join(' ').toLowerCase();
    pageTextCache.set(p, text);
    return text;
  }

  async function runSearch(): Promise<void> {
    const query = searchQuery.trim().toLowerCase();
    if (!query || !pdfDoc) {
      searchHits = [];
      searchPos = -1;
      applySearchHighlights();
      return;
    }
    searching = true;
    try {
      const hits: SearchHit[] = [];
      for (let p = 1; p <= numPages; p += 1) {
        const text = await pageText(p);
        let from = 0;
        let index = 0;
        for (;;) {
          const at = text.indexOf(query, from);
          if (at === -1) break;
          hits.push({ page: p, index });
          index += 1;
          from = at + query.length;
        }
      }
      searchHits = hits;
      searchPos = hits.length ? 0 : -1;
      if (hits.length) {
        const target = hits[0];
        if (target.page === currentPage) applySearchHighlights();
        else goTo(target.page);
      } else {
        applySearchHighlights();
      }
    } finally {
      searching = false;
    }
  }

  function stepSearch(delta: number): void {
    if (!searchHits.length) return;
    searchPos = (searchPos + delta + searchHits.length) % searchHits.length;
    const target = searchHits[searchPos];
    if (target.page === currentPage) applySearchHighlights();
    else goTo(target.page);
  }

  // Highlight matching spans of the rendered text layer for the current page.
  function applySearchHighlights(): void {
    if (!textLayerEl) return;
    const query = searchQuery.trim().toLowerCase();
    const spans = Array.from(textLayerEl.querySelectorAll('span')) as HTMLSpanElement[];
    for (const span of spans) span.classList.remove('search-hl', 'current');
    if (!query || !searchHits.length) return;
    const activeHit = searchHits[searchPos];
    let matched = -1;
    let first: HTMLSpanElement | null = null;
    let active: HTMLSpanElement | null = null;
    for (const span of spans) {
      const text = (span.textContent ?? '').toLowerCase();
      if (!text.includes(query)) continue;
      span.classList.add('search-hl');
      first ??= span;
      matched += 1;
      if (activeHit && activeHit.page === currentPage && matched === activeHit.index) {
        span.classList.add('current');
        active = span;
      }
    }
    (active ?? first)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  // --- citation ↔ reference navigation ------------------------------------
  function jumpToContext(context: CitationContext): void {
    const box = context.pdf_coordinates?.[0];
    tab = 'pdf';
    if (box) {
      goTo(box.page);
      flash(`ctx:${context.id}`);
    } else if (context.page) {
      goTo(context.page);
    }
  }

  // Direction A: an in-text overlay was clicked → ask the parent to reveal the reference,
  // and flash the matching context entry within the reader's References tab as well.
  function onOverlayClick(context: CitationContext): void {
    flash(`ctx:${context.id}`);
    if (context.reference_id && onNavigateToReference) {
      onNavigateToReference(context.reference_id);
    } else {
      tab = 'contexts';
    }
  }

  // Direction B: jump to the first in-text mention of a reference, cycling on repeat.
  let mentionCycle = -1;
  async function jumpToReferenceMention(referenceId: string): Promise<void> {
    const mentions = contexts.filter(
      (c) => c.reference_id === referenceId && (c.pdf_coordinates?.length ?? 0) > 0,
    );
    if (!mentions.length) return;
    mentionCycle = (mentionCycle + 1) % mentions.length;
    const context = mentions[mentionCycle];
    const box = context.pdf_coordinates?.[0];
    tab = 'pdf';
    if (box) {
      goTo(box.page);
      await tick();
      flash(`ctx:${context.id}`);
    }
  }

  // --- selection → annotation --------------------------------------------
  function captureSelection(): void {
    const selection = window.getSelection();
    const text = selection?.toString().trim() ?? '';
    if (!text) return;
    selectedText = text;
    annotationPage = String(currentPage);
    annotationType = 'highlight';
    tab = 'annotations';
    selectionBoxes = boxesForSelection(selection);
  }

  // Map a DOM selection to one top-left-origin box per visual line, in the same scaled-pixel
  // convention the citation overlays use (box.x * scale ⇒ device px). This matches GROBID's
  // top-left coordinate frame and round-trips through boxToStyle() at any zoom level.
  function boxesForSelection(selection: Selection | null): PdfCoordinateBox[] | null {
    if (!selection?.rangeCount || !canvasEl) return null;
    const canvasRect = canvasEl.getBoundingClientRect();
    const rects = Array.from(selection.getRangeAt(0).getClientRects()).filter((r) => r.width > 0);
    if (!rects.length) return null;
    const round = (v: number) => Math.round(v * 100) / 100;
    return rects.map((r) => ({
      page: currentPage,
      x: round((r.left - canvasRect.left) / scale),
      y: round((r.top - canvasRect.top) / scale),
      w: round(r.width / scale),
      h: round(r.height / scale),
    }));
  }

  async function createAnnotation(): Promise<void> {
    if (!onCreateAnnotation) return;
    await onCreateAnnotation({
      annotation_type: annotationType,
      page: annotationPage ? Number(annotationPage) : null,
      selected_text: selectedText || null,
      content_markdown: annotationContent || null,
      coordinates: selectionBoxes ? { boxes: selectionBoxes } : null,
    });
    selectedText = '';
    annotationContent = '';
    selectionBoxes = null;
  }

  async function removeAnnotation(annotationId: string): Promise<void> {
    if (!onDeleteAnnotation) return;
    await onDeleteAnnotation(annotationId);
  }

  // Click a note → jump to its page/anchor and flash the highlight box.
  function jumpToAnnotation(annotation: Annotation): void {
    tab = 'pdf';
    const boxes = (annotation.coordinates as { boxes?: PdfCoordinateBox[] } | null)?.boxes ?? [];
    const target = boxes[0]?.page ?? annotation.page;
    if (target) goTo(target);
    flash(`ann:${annotation.id}`);
  }

  // --- drag-to-pan (space-held or middle mouse) ---------------------------
  let panning = false;
  let spaceHeld = false;
  let panStart = { x: 0, y: 0, left: 0, top: 0 };
  let pageWrapEl: HTMLDivElement | null = null;
  function onPanKeyDown(e: KeyboardEvent): void {
    if (e.code === 'Space' && !spaceHeld && e.target === pageWrapEl) {
      spaceHeld = true;
      e.preventDefault();
    }
  }
  function onPanKeyUp(e: KeyboardEvent): void {
    if (e.code === 'Space') spaceHeld = false;
  }
  function onPanPointerDown(e: PointerEvent): void {
    // Pan only via middle mouse or while Space is held, so text selection still works.
    if (!pageWrapEl) return;
    if (e.button !== 1 && !spaceHeld) return;
    panning = true;
    panStart = {
      x: e.clientX,
      y: e.clientY,
      left: pageWrapEl.scrollLeft,
      top: pageWrapEl.scrollTop,
    };
    pageWrapEl.setPointerCapture(e.pointerId);
    e.preventDefault();
  }
  function onPanPointerMove(e: PointerEvent): void {
    if (!panning || !pageWrapEl) return;
    pageWrapEl.scrollLeft = panStart.left - (e.clientX - panStart.x);
    pageWrapEl.scrollTop = panStart.top - (e.clientY - panStart.y);
  }
  function onPanPointerUp(e: PointerEvent): void {
    if (!panning || !pageWrapEl) return;
    panning = false;
    try {
      pageWrapEl.releasePointerCapture(e.pointerId);
    } catch {
      // Pointer already released.
    }
  }

  // --- smooth-scroll mode -------------------------------------------------
  // Lazily render pages as they scroll into view; track currentPage from the most-visible page.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const scrollCanvases = new Map<number, HTMLCanvasElement>();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const scrollLayers = new Map<number, HTMLDivElement>();
  const scrollRendered = new Set<number>();
  let scrollObserver: IntersectionObserver | null = null;

  function registerScrollPage(node: HTMLElement, n: number): { destroy(): void } {
    node.dataset.page = String(n);
    if (!scrollObserver) setupScrollObserver();
    scrollObserver?.observe(node);
    return {
      destroy() {
        scrollObserver?.unobserve(node);
        scrollCanvases.delete(n);
        scrollLayers.delete(n);
        scrollRendered.delete(n);
      },
    };
  }

  function bindScrollCanvas(node: HTMLCanvasElement, n: number): void {
    scrollCanvases.set(n, node);
  }
  function bindScrollLayer(node: HTMLDivElement, n: number): void {
    scrollLayers.set(n, node);
  }

  async function renderScrollPage(n: number): Promise<void> {
    if (scrollRendered.has(n)) return;
    const canvas = scrollCanvases.get(n);
    const layer = scrollLayers.get(n) ?? null;
    if (!canvas) return;
    scrollRendered.add(n);
    await renderPageInto(n, canvas, layer, scale);
  }

  function setupScrollObserver(): void {
    scrollObserver?.disconnect();
    scrollObserver = new IntersectionObserver(
      (entries) => {
        let best: { n: number; ratio: number } | null = null;
        for (const entry of entries) {
          const n = Number((entry.target as HTMLElement).dataset.page);
          if (entry.isIntersecting) void renderScrollPage(n);
          if (!best || entry.intersectionRatio > best.ratio) {
            best = { n, ratio: entry.intersectionRatio };
          }
        }
        if (best && best.ratio > 0) currentPage = best.n;
      },
      { threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
  }

  // Re-render scroll pages when scale changes (clear cache so visible pages repaint).
  function resetScrollRender(): void {
    scrollRendered.clear();
    for (const [n, canvas] of scrollCanvases) {
      const layer = scrollLayers.get(n) ?? null;
      void renderPageInto(n, canvas, layer, scale).then(() => scrollRendered.add(n));
    }
  }

  // Load (or reload) whenever a new PDF URL is shown in the PDF tab.
  $: if (tab === 'pdf' && fileUrl && fileUrl !== loadedUrl && !loadingPdf) {
    void loadPdf(fileUrl);
  }
  // Re-render after the canvas element mounts for an already-loaded doc (paged mode).
  $: if (tab === 'pdf' && viewMode === 'paged' && canvasEl && pdfDoc && loadedUrl === fileUrl) {
    void renderPage(currentPage);
  }
  // Wire up the IntersectionObserver when entering scroll mode.
  $: if (tab === 'pdf' && viewMode === 'scroll' && pdfDoc && !scrollObserver) {
    setupScrollObserver();
  }

  onDestroy(() => {
    if (renderTask) renderTask.cancel();
    if (currentTextLayer) {
      try {
        currentTextLayer.cancel();
      } catch {
        // ignore
      }
    }
    scrollObserver?.disconnect();
    if (pdfDoc) void pdfDoc.destroy().catch(() => undefined);
  });
</script>

<svelte:window on:keydown={onPanKeyDown} on:keyup={onPanKeyUp} />

<section class="reader">
  <header>
    <div>
      <h3>{fileName}</h3>
      <span>{fileId.slice(0, 8)}</span>
    </div>
    <nav aria-label="Reader panels">
      <button type="button" class:active={tab === 'pdf'} on:click={() => (tab = 'pdf')}>Paper</button>
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
      <p class="empty">Open a paper in the reader</p>
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
        <div class="mode" role="group" aria-label="View mode">
          <button
            type="button"
            class:active={viewMode === 'paged'}
            on:click={() => setViewMode('paged')}
            title="One page at a time"
          >
            Paged
          </button>
          <button
            type="button"
            class:active={viewMode === 'scroll'}
            on:click={() => setViewMode('scroll')}
            title="Continuous smooth scroll through all pages"
          >
            Scroll
          </button>
        </div>
        <form class="search" on:submit|preventDefault={runSearch}>
          <input
            bind:value={searchQuery}
            placeholder="Search whole paper…"
            title="In-app search scans the whole paper. Browser Ctrl+F searches only the visible page."
          />
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
      <p class="search-hint">
        In-app search scans the whole paper; browser Ctrl+F finds text on the visible page. Pan a
        zoomed page by holding Space (or middle-mouse) and dragging.
      </p>

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
        <!-- svelte-ignore a11y-no-static-element-interactions -->
        <div
          class="page-wrap"
          class:panning
          class:pannable={spaceHeld}
          bind:this={pageWrapEl}
          tabindex="-1"
          on:pointerdown={onPanPointerDown}
          on:pointermove={onPanPointerMove}
          on:pointerup={onPanPointerUp}
          on:pointerleave={onPanPointerUp}
        >
          {#if loadingPdf}<p class="empty">Rendering…</p>{/if}
          {#if viewMode === 'paged'}
            <div
              class="canvas-stage"
              style={`width:${pageWidth}px;height:${pageHeight}px;--scale-factor:${scale}`}
            >
              <canvas bind:this={canvasEl}></canvas>
              <div class="textLayer" bind:this={textLayerEl}></div>
              {#each pageBoxes as item (item.context.id + ':' + item.box.x + ',' + item.box.y)}
                <button
                  type="button"
                  class="overlay"
                  class:flash={flashKey === `ctx:${item.context.id}`}
                  title={item.context.reference_title ??
                    item.context.marker_text ??
                    'citation'}
                  on:click={() => onOverlayClick(item.context)}
                  style={boxToStyle(item.box)}
                ></button>
              {/each}
              {#each annotationBoxes as item (item.annotation.id + ':' + item.box.x + ',' + item.box.y)}
                <div
                  class="annotation-overlay"
                  class:flash={flashKey === `ann:${item.annotation.id}`}
                  title={item.annotation.selected_text ??
                    item.annotation.content_markdown ??
                    'annotation'}
                  style={boxToStyle(item.box)}
                ></div>
              {/each}
            </div>
          {:else}
            <div class="scroll-stack">
              {#each Array(numPages) as _, i (i)}
                <div
                  class="canvas-stage scroll-page"
                  style={`--scale-factor:${scale}`}
                  use:registerScrollPage={i + 1}
                >
                  <canvas use:bindScrollCanvas={i + 1}></canvas>
                  <div class="textLayer" use:bindScrollLayer={i + 1}></div>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    {/if}
  {:else if tab === 'contexts'}
    {#if contexts.length === 0}
      <p class="empty">No citation contexts extracted</p>
    {:else}
      <div class="context-list">
        {#each contexts as context (context.id)}
          <article class:flash={flashKey === `ctx:${context.id}`}>
            <header>
              <strong>{context.marker_text ?? 'citation'}</strong>
              <span>{context.section_label ?? 'section unknown'}</span>
            </header>
            <p>{context.context_sentence ?? 'No sentence context'}</p>
            <small>
              {context.reference_title ?? context.reference_raw_citation ?? 'Unparsed reference'}
            </small>
            <div class="ctx-actions">
              {#if (context.pdf_coordinates?.length ?? 0) > 0 || context.page}
                <button type="button" class="jump" on:click={() => jumpToContext(context)}>
                  Jump to p.{context.pdf_coordinates?.[0]?.page ?? context.page}
                </button>
              {/if}
              {#if context.reference_id && onNavigateToReference}
                <button
                  type="button"
                  class="jump"
                  on:click={() => onNavigateToReference?.(context.reference_id)}
                >
                  Show reference
                </button>
              {/if}
            </div>
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
      {#if selectionBoxes?.length}
        <small class="coord-note">
          Anchored at p.{selectionBoxes[0].page} ({selectionBoxes.length} box{selectionBoxes.length >
          1
            ? 'es'
            : ''} from selection)
        </small>
      {/if}
      <button type="submit" disabled={!onCreateAnnotation || (!selectedText && !annotationContent)}>
        Add
      </button>
    </form>

    {#if annotations.length === 0}
      <p class="empty">No annotations</p>
    {:else}
      <div class="annotation-list">
        {#each annotations as annotation (annotation.id)}
          <article class:flash={flashKey === `ann:${annotation.id}`}>
            <header>
              <button type="button" class="ann-jump" on:click={() => jumpToAnnotation(annotation)}>
                <strong>{annotation.annotation_type.replaceAll('_', ' ')}</strong>
                <span>page {annotation.page ?? '-'}</span>
              </button>
              {#if onDeleteAnnotation}
                <button
                  type="button"
                  class="ann-delete"
                  aria-label="Delete annotation"
                  title="Delete this note"
                  on:click={() => removeAnnotation(annotation.id)}
                >
                  ✕
                </button>
              {/if}
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
  .mode,
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

  .search-hint {
    color: #667381;
    font-size: 0.74rem;
    margin: -0.3rem 0 0;
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
    outline: none;
    overflow: auto;
    padding: 0.6rem;
  }

  .page-wrap.pannable {
    cursor: grab;
  }

  .page-wrap.panning {
    cursor: grabbing;
    user-select: none;
  }

  .scroll-stack {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }

  .canvas-stage {
    position: relative;
  }

  .canvas-stage canvas {
    display: block;
  }

  /* PDF.js text layer — transparent selectable text aligned over the canvas. */
  .textLayer {
    color: transparent;
    inset: 0;
    line-height: 1;
    overflow: hidden;
    position: absolute;
    text-align: initial;
    transform-origin: 0 0;
  }

  .textLayer :global(span),
  .textLayer :global(br) {
    color: transparent;
    cursor: text;
    position: absolute;
    transform-origin: 0 0;
    white-space: pre;
  }

  .textLayer :global(span.search-hl) {
    background: rgba(255, 214, 0, 0.45);
    border-radius: 2px;
  }

  .textLayer :global(span.search-hl.current) {
    background: rgba(255, 138, 0, 0.6);
  }

  .textLayer :global(span::selection) {
    background: rgba(0, 110, 230, 0.35);
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

  .annotation-overlay {
    background: rgba(120, 200, 120, 0.28);
    border: 1px solid rgba(40, 150, 60, 0.6);
    border-radius: 2px;
    pointer-events: none;
    position: absolute;
  }

  .overlay.flash,
  .annotation-overlay.flash {
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

  .ctx-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
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

  .context-list article.flash,
  .annotation-list article.flash {
    animation: flash-card 0.6s ease-in-out 2;
  }

  @keyframes flash-card {
    50% {
      background: rgba(255, 200, 90, 0.6);
    }
  }

  .context-list article header,
  .annotation-list article header {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
    margin-bottom: 0.35rem;
  }

  .ann-jump {
    align-items: center;
    background: none;
    border: none;
    display: flex;
    flex: 1;
    gap: 0.5rem;
    justify-content: flex-start;
    min-height: 0;
    padding: 0;
    text-align: left;
  }

  .ann-delete {
    border-color: #e0b4b0;
    color: #b3261e;
    min-height: 1.8rem;
    padding: 0.15rem 0.45rem;
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
