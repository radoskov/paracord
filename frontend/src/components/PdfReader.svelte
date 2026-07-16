<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import { get } from 'svelte/store';

  import type { Annotation, CitationContext, PdfCoordinateBox } from '../api/client';
  import {
    annotationBoxesForPage,
    citationBoxesForPage,
    overlayBoxStyle,
  } from '../lib/reader/overlayBoxes';
  import {
    readStoredReadingMode,
    readingModeFilter,
    writeReadingMode,
    type ReadingMode,
  } from '../lib/reader/readingMode';
  import { canEdit, INSUFFICIENT_ROLE } from '../lib/session';

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
  // Optional server-side PDF text fetcher (GET /files/{id}/text): native text layer, else on-the-fly
  // OCR. Used as the search / "copy text" fallback for scanned/OCR'd PDFs whose in-browser pdf.js
  // text layer is empty. Normal PDFs keep using the pdf.js text layer.
  export let onFetchText: (() => Promise<{ text: string; source: string }>) | null = null;
  // Whether the viewer may add/delete annotations on this paper. Defaults to the global edit floor
  // (contributor+); the host passes the per-paper "can modify this paper" decision so a contributor
  // can only annotate their own papers.
  export let canAnnotate: boolean = get(canEdit);

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
  // Unscaled page-1 dimensions — used to RESERVE space for not-yet-rendered scroll pages so the
  // total height is stable and a jump to a far page lands correctly instead of the lazy renderer
  // "fighting" a smooth scroll as intervening pages grow from 0 height (2026-07-16).
  let basePageWidth = 0;
  let basePageHeight = 0;
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

  // --- reading mode (page-canvas easing) ----------------------------------
  // Opt-in easing applied to the rendered page canvas ONLY (see `--page-filter` below), so the
  // text-selection layer and the highlight / citation / annotation overlays stay true.
  let readingMode: ReadingMode = readStoredReadingMode();
  const READING_MODE_OPTIONS: { value: ReadingMode; label: string; title: string }[] = [
    { value: 'original', label: 'Original', title: 'Render the page true-to-original' },
    { value: 'dim', label: 'Dim', title: 'Warm, gently dimmed cream page — easier on the eyes' },
    { value: 'dark', label: 'Dark', title: 'Dark page, light text (smart invert)' },
  ];
  function setReadingMode(mode: ReadingMode): void {
    if (mode === readingMode) return;
    readingMode = mode;
    writeReadingMode(mode);
  }
  $: pageFilter = readingModeFilter(readingMode);

  // Zen mode (UX batch 3): the reader takes over the viewport on a dark backdrop; only paging,
  // zoom, view-mode and reading-mode controls remain (plus Exit zen / Esc). Combinable with any
  // reading mode — it changes layout, not the page filter.
  let zen = false;
  let readerEl: HTMLElement | undefined;
  let zenAnchor: Comment | null = null;
  function toggleZen(): void {
    if (zen) {
      exitZen();
      return;
    }
    zen = true;
    tab = 'pdf'; // the tab nav is hidden in zen — make sure the pages are what shows
    // Portal to <body> (UX batch 3 fix): the reader usually lives inside the paper-view modal,
    // whose panel creates a containing block — position:fixed alone left the app visible around
    // the backdrop. Moving the element out for the duration of zen makes inset:0 truly viewport-
    // sized; the comment anchor restores the original spot on exit.
    if (readerEl?.parentNode && typeof document !== 'undefined') {
      zenAnchor = document.createComment('zen-anchor');
      readerEl.parentNode.insertBefore(zenAnchor, readerEl);
      document.body.appendChild(readerEl);
    }
  }
  function exitZen(): void {
    zen = false;
    if (readerEl && zenAnchor?.parentNode) {
      zenAnchor.parentNode.insertBefore(readerEl, zenAnchor);
    }
    zenAnchor?.remove();
    zenAnchor = null;
  }
  function onZenKeydown(ev: KeyboardEvent): void {
    if (zen && ev.key === 'Escape') {
      ev.preventDefault();
      // Swallow the event so the paper-view modal's own Escape handler doesn't ALSO fire and close
      // the whole reader. This is registered in the CAPTURE phase (see onMount) so it runs BEFORE
      // the Modal's bubble-phase window listener — the previous bubble-phase registration was
      // listener-order dependent and let the modal close first, leaving zen orphaned (2026-07-16).
      ev.stopImmediatePropagation();
      exitZen();
    }
  }

  onMount(() => {
    window.addEventListener('keydown', onZenKeydown, true); // capture: before the Modal's handler
    return () => window.removeEventListener('keydown', onZenKeydown, true);
  });

  // --- search -------------------------------------------------------------
  // Separator joining adjacent text items when building a page's full-text string. A single
  // space lets phrases that span item boundaries (the common case) match, while keeping the
  // offset→span mapping deterministic (every join contributes exactly one character).
  const ITEM_SEP = ' ';
  let searchQuery = '';
  // One entry per occurrence across the whole document. `start`/`end` are character offsets
  // into the page's concatenated text so the matching spans can be recovered for highlighting.
  type SearchHit = { page: number; start: number; end: number };
  let searchHits: SearchHit[] = [];
  let searchPos = -1;
  let searching = false;
  // Lazily-built per-page cache: the lowercased concatenated page text used for the whole-doc
  // scan. The same join (ITEM_SEP) is replayed over the rendered spans to map offsets back.
  const pageTextCache = new Map<number, string>();

  // --- server-text fallback (scanned / OCR'd PDFs) -----------------------
  // Below this many non-space native chars across the whole doc, the pdf.js text layer is treated
  // as empty (a scanned PDF), so search + copy fall back to the server-extracted text.
  const NATIVE_SPARSE_THRESHOLD = 100;
  let serverText: string | null = null; // full server-extracted text, cached (null = not fetched)
  let serverTextSource = ''; // 'native' | 'ocr' | 'none'
  let serverTextLoading = false;
  let usedServerFallback = false; // the last search fell back to server text (no span highlights)
  let serverSearchCount = 0; // match count in the server text (fallback mode)
  let copyStatus = '';

  // --- selection → annotation --------------------------------------------
  // Generalised flash key, shared by citation overlays, annotation boxes and references.
  let flashKey = '';
  function flash(key: string): void {
    flashKey = key;
    window.setTimeout(() => {
      if (flashKey === key) flashKey = '';
    }, 2000);
  }

  // In-reader status line (2026-07-16) — reader actions (annotation add/delete) report HERE, not via
  // the host paper-view message that used to leak "Annotation deleted" under the paper title.
  let readerStatus = '';
  let readerStatusError = false;
  function setReaderStatus(msg: string, isError = false): void {
    readerStatus = msg;
    readerStatusError = isError;
    window.setTimeout(() => {
      if (readerStatus === msg) readerStatus = '';
    }, 2500);
  }

  // Scroll the page container so a box (unscaled, top-left origin) is in view — used by jump-to
  // for annotations / citations / references, so they land ON the anchor, not just the page top
  // (works in both paged and scroll modes; 2026-07-16).
  async function scrollToBox(box: PdfCoordinateBox | undefined): Promise<void> {
    if (!box || !pageWrapEl) return;
    await tick();
    const canvas = viewMode === 'scroll' ? scrollCanvases.get(box.page) : canvasEl;
    const stage = canvas?.parentElement as HTMLElement | null;
    if (!stage) return;
    const wrapRect = pageWrapEl.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    const top = stageRect.top - wrapRect.top + pageWrapEl.scrollTop + box.y * scale;
    const left = stageRect.left - wrapRect.left + pageWrapEl.scrollLeft + box.x * scale;
    pageWrapEl.scrollTo({
      top: Math.max(0, top - 80),
      left: Math.max(0, left - 40),
      behavior: 'auto',
    });
  }

  // --- annotation form ----------------------------------------------------
  let annotationType = 'note';
  let annotationPage = '';
  let selectedText = '';
  let annotationContent = '';
  let selectionBoxes: PdfCoordinateBox[] | null = null;

  // Overlay boxes (citation anchors + annotation highlights) are derived per page from the SAME
  // scale the page canvas is rendered at (see lib/reader/overlayBoxes), so they track the text
  // across zoom, resize and re-render. The paged view shows one page; the scroll view builds the
  // same overlays per page (each scroll page owns its own canvas + overlay layer at `scale`).
  // Takes `scale` explicitly so the template attribute re-evaluates on zoom (Svelte only tracks
  // deps referenced in the expression — reading `scale` inside the fn was NOT tracked, so overlays
  // drifted off their marks after +/- until a page/mode change; 2026-07-16).
  function boxToStyle(box: PdfCoordinateBox, s: number): string {
    return overlayBoxStyle(box, s);
  }

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
      const firstViewport = (await doc.getPage(1)).getViewport({ scale: 1 });
      basePageWidth = firstViewport.width;
      basePageHeight = firstViewport.height;
      searchHits = [];
      searchPos = -1;
      pageTextCache.clear();
      // Reset the server-text fallback cache for the newly-loaded document.
      serverText = null;
      serverTextSource = '';
      usedServerFallback = false;
      serverSearchCount = 0;
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
    // Mode-aware: in scroll mode renderPage no-ops (no bound canvas), so route through the
    // scroll-aware jump — the page buttons/pager now work in both modes (2026-07-16).
    void goToPageOnPaperTab(n);
  }

  // Switch to the Paper tab and navigate to a page, awaiting the canvas mount + render so a
  // follow-up flash lands on a painted page. Without the tick() the canvas/textLayer may not be
  // bound yet (we just switched tabs), and renderPage() would early-return — the page would not
  // change and the flash would target nothing.
  async function goToPageOnPaperTab(n: number): Promise<void> {
    tab = 'pdf';
    await tick();
    if (viewMode === 'scroll') {
      currentPage = Math.min(Math.max(1, n), numPages || n);
      await tick();
      // Instant (not smooth): with reserved page heights the target offset is stable, so one jump
      // lands correctly. A smooth scroll to a far page got "fought" by the lazy renderer growing
      // intervening pages mid-animation and stalled around page 4 (2026-07-16).
      scrollCanvases.get(currentPage)?.scrollIntoView({ block: 'start', behavior: 'auto' });
      await renderScrollPage(currentPage);
    } else {
      await renderPage(n);
    }
  }

  function zoom(delta: number): void {
    scale = Math.min(3, Math.max(0.5, Math.round((scale + delta) * 10) / 10));
    if (viewMode === 'scroll') resetScrollRender();
    else void renderPage(currentPage);
  }

  // --- whole-document search ----------------------------------------------
  // The page's full text is the concatenation of every text item joined by ITEM_SEP, so a query
  // phrase that crosses item (span) boundaries still matches. The SAME concatenation is replayed
  // over the rendered text-layer spans (see spanOffsets) to recover which spans to highlight.
  async function pageText(p: number): Promise<string> {
    const cached = pageTextCache.get(p);
    if (cached !== undefined) return cached;
    const page = await pdfDoc.getPage(p);
    const content = await page.getTextContent();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const text = content.items.map((item: any) => item.str ?? '').join(ITEM_SEP).toLowerCase();
    pageTextCache.set(p, text);
    return text;
  }

  // Lazily fetch + cache the server-extracted text (native layer, else on-the-fly OCR).
  async function ensureServerText(): Promise<string> {
    if (serverText !== null) return serverText;
    if (!onFetchText) {
      serverText = '';
      serverTextSource = 'none';
      return '';
    }
    serverTextLoading = true;
    try {
      const r = await onFetchText();
      serverText = r.text ?? '';
      serverTextSource = r.source ?? 'none';
    } catch {
      serverText = '';
      serverTextSource = 'none';
    } finally {
      serverTextLoading = false;
    }
    return serverText;
  }

  // True when the whole-document pdf.js text layer is near-empty (a scanned PDF), so the native
  // span-based search would find nothing and we should fall back to the server-extracted text.
  async function nativeDocIsSparse(): Promise<boolean> {
    let total = 0;
    for (let p = 1; p <= numPages; p += 1) {
      total += (await pageText(p)).replace(/\s/g, '').length;
      if (total >= NATIVE_SPARSE_THRESHOLD) return false;
    }
    return true;
  }

  // Copy the paper's full text to the clipboard, using the server text (OCR fallback) when the
  // in-browser text layer is empty — so scanned/OCR'd PDFs are still copyable.
  async function copyExtractedText(): Promise<void> {
    const text = await ensureServerText();
    if (!text.trim()) {
      copyStatus = 'No text';
    } else {
      try {
        await navigator.clipboard.writeText(text);
        copyStatus = `Copied (${serverTextSource})`;
      } catch {
        copyStatus = 'Copy failed';
      }
    }
    window.setTimeout(() => {
      copyStatus = '';
    }, 1800);
  }

  async function runSearch(): Promise<void> {
    const query = searchQuery.trim().toLowerCase();
    if (!query || !pdfDoc) {
      searchHits = [];
      searchPos = -1;
      usedServerFallback = false;
      serverSearchCount = 0;
      applySearchHighlights();
      return;
    }
    searching = true;
    try {
      const hits: SearchHit[] = [];
      for (let p = 1; p <= numPages; p += 1) {
        const text = await pageText(p);
        let from = 0;
        for (;;) {
          const at = text.indexOf(query, from);
          if (at === -1) break;
          hits.push({ page: p, start: at, end: at + query.length });
          from = at + query.length;
        }
      }
      searchHits = hits;
      searchPos = hits.length ? 0 : -1;
      // Fallback for scanned / OCR'd PDFs: the pdf.js text layer is empty, so the native scan finds
      // nothing. Count matches in the server-extracted text (native layer or on-the-fly OCR). We
      // can't highlight on-page spans (there are none), so this reports the match count only.
      usedServerFallback = false;
      serverSearchCount = 0;
      if (!hits.length && onFetchText && (await nativeDocIsSparse())) {
        const text = (await ensureServerText()).toLowerCase();
        if (text) {
          let from = 0;
          let count = 0;
          for (;;) {
            const at = text.indexOf(query, from);
            if (at === -1) break;
            count += 1;
            from = at + query.length;
          }
          serverSearchCount = count;
          usedServerFallback = true;
        }
      }
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

  // Map each rendered text-layer span to its [start,end) char range in the page's concatenated
  // text, replaying the ITEM_SEP join. The text layer renders one span per text item in document
  // order, so walking the spans reproduces the same offsets used when scanning for matches.
  function spanOffsets(spans: HTMLSpanElement[]): { start: number; end: number }[] {
    const ranges: { start: number; end: number }[] = [];
    let offset = 0;
    spans.forEach((span, i) => {
      const len = (span.textContent ?? '').length;
      ranges.push({ start: offset, end: offset + len });
      offset += len;
      // A separator sits between consecutive items, matching items.join(ITEM_SEP).
      if (i < spans.length - 1) offset += ITEM_SEP.length;
    });
    return ranges;
  }

  // Highlight matching spans of the rendered text layer for the current page. A hit is a char
  // range in the page text; every span that overlaps a hit range is highlighted, so phrases that
  // span multiple items light up all their pieces.
  function applySearchHighlights(): void {
    if (!textLayerEl) return;
    const spans = Array.from(textLayerEl.querySelectorAll('span')) as HTMLSpanElement[];
    for (const span of spans) span.classList.remove('search-hl', 'current');
    if (!searchQuery.trim() || !searchHits.length) return;
    const ranges = spanOffsets(spans);
    const pageHits = searchHits.filter((h) => h.page === currentPage);
    if (!pageHits.length) return;
    const activeHit = searchHits[searchPos];
    const overlaps = (h: SearchHit, r: { start: number; end: number }): boolean =>
      r.start < h.end && r.end > h.start;
    let first: HTMLSpanElement | null = null;
    let active: HTMLSpanElement | null = null;
    spans.forEach((span, i) => {
      const r = ranges[i];
      if (!pageHits.some((h) => overlaps(h, r))) return;
      span.classList.add('search-hl');
      first ??= span;
      if (activeHit && activeHit.page === currentPage && overlaps(activeHit, r)) {
        span.classList.add('current');
        active ??= span;
      }
    });
    (active ?? first)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  // --- citation ↔ reference navigation ------------------------------------
  async function jumpToContext(context: CitationContext): Promise<void> {
    const box = context.pdf_coordinates?.[0];
    const page = box?.page ?? context.page;
    if (!page) return;
    await goToPageOnPaperTab(page);
    await scrollToBox(box);
    flash(`ctx:${context.id}`);
  }

  // Scroll-to + flash a context entry inside the reader's own References (contexts) tab.
  async function revealContextInTab(contextId: string): Promise<void> {
    tab = 'contexts';
    await tick();
    const el = document.getElementById(`reader-ctx-${contextId}`);
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    flash(`ctx:${contextId}`);
  }

  // Direction A: an in-text overlay was clicked → reveal the reference in BOTH places: the
  // parent paper view's References block (via the callback) and the reader's own References tab.
  function onOverlayClick(context: CitationContext): void {
    if (context.reference_id && onNavigateToReference) {
      onNavigateToReference(context.reference_id);
    }
    void revealContextInTab(context.id);
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
    if (box) {
      await goToPageOnPaperTab(box.page);
      await scrollToBox(box);
      flash(`ctx:${context.id}`);
    }
  }

  // --- selection → annotation --------------------------------------------
  function captureSelection(): void {
    if (!canAnnotate) return;
    const selection = window.getSelection();
    // De-hyphenate line-break splits ("sum-\nmarize" → "summarize") and collapse whitespace, so the
    // stored highlight text is a clean, matchable string (2026-07-16). Only a letter-hyphen-
    // whitespace-letter run is joined, so real hyphens ("state-of-the-art") are preserved.
    const text = (selection?.toString() ?? '')
      .replace(/(\p{L})-\s+(\p{L})/gu, '$1$2')
      .replace(/\s+/g, ' ')
      .trim();
    if (!text) return;
    selectedText = text;
    selectionBoxes = boxesForSelection(selection);
    // Page comes from the selection's own page (scroll mode can select any page), not the
    // most-visible currentPage.
    annotationPage = String(selectionBoxes?.[0]?.page ?? currentPage);
    annotationType = 'highlight';
    tab = 'annotations';
  }

  // Which canvas + page the selection sits on. In paged mode there's one canvas (currentPage); in
  // scroll mode the selection can be on ANY page, so find the enclosing .canvas-stage and read its
  // data-page + own canvas — otherwise boxes were computed against the wrong (or a null) canvas, so
  // new highlights in scroll mode captured nothing (2026-07-16).
  function selectionTarget(
    selection: Selection | null,
  ): { canvas: HTMLCanvasElement | null; page: number } {
    const node = selection?.anchorNode ?? null;
    const el = node instanceof Element ? node : (node?.parentElement ?? null);
    const stage = el?.closest?.('.canvas-stage') as HTMLElement | null;
    const canvas = (stage?.querySelector('canvas') as HTMLCanvasElement | null) ?? canvasEl;
    const page = stage?.dataset.page ? Number(stage.dataset.page) : currentPage;
    return { canvas, page };
  }

  // Map a DOM selection to one top-left-origin box per visual line, in the same scaled-pixel
  // convention the citation overlays use (box.x * scale ⇒ device px). This matches GROBID's
  // top-left coordinate frame and round-trips through boxToStyle() at any zoom level.
  function boxesForSelection(selection: Selection | null): PdfCoordinateBox[] | null {
    if (!selection?.rangeCount) return null;
    const { canvas, page } = selectionTarget(selection);
    if (!canvas) return null;
    const canvasRect = canvas.getBoundingClientRect();
    const rects = Array.from(selection.getRangeAt(0).getClientRects()).filter((r) => r.width > 0);
    if (!rects.length) return null;
    const round = (v: number) => Math.round(v * 100) / 100;
    return rects.map((r) => ({
      page,
      x: round((r.left - canvasRect.left) / scale),
      y: round((r.top - canvasRect.top) / scale),
      w: round(r.width / scale),
      h: round(r.height / scale),
    }));
  }

  async function createAnnotation(): Promise<void> {
    if (!onCreateAnnotation) return;
    try {
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
      setReaderStatus('Annotation added');
    } catch {
      setReaderStatus('Could not add the annotation', true);
    }
  }

  async function removeAnnotation(annotationId: string): Promise<void> {
    if (!onDeleteAnnotation) return;
    try {
      await onDeleteAnnotation(annotationId);
      setReaderStatus('Annotation deleted');
    } catch {
      setReaderStatus('Could not delete the annotation', true);
    }
  }

  // Click a note → jump to its page/anchor and flash the highlight box. Sequence the tab switch
  // + render before flashing so the anchor exists on a painted page (see goToPageOnPaperTab).
  async function jumpToAnnotation(annotation: Annotation): Promise<void> {
    const boxes = (annotation.coordinates as { boxes?: PdfCoordinateBox[] } | null)?.boxes ?? [];
    const target = boxes[0]?.page ?? annotation.page;
    if (!target) return;
    await goToPageOnPaperTab(target);
    await scrollToBox(boxes[0]);
    flash(`ann:${annotation.id}`);
  }

  // --- drag-to-pan (space-held or middle mouse) ---------------------------
  let panning = false;
  let spaceHeld = false;
  let panStart = { x: 0, y: 0, left: 0, top: 0 };
  let pageWrapEl: HTMLDivElement | null = null;
  function onPanKeyDown(e: KeyboardEvent): void {
    // 2026-07-16: the old guard required the page-wrap to be the keydown target, but it's rarely
    // focused, so Space went to <body> → hold-space just scrolled and never enabled pan. Now enable
    // pan whenever the page area exists and focus isn't in a text field (so search typing is safe),
    // and preventDefault so Space doesn't scroll the page.
    if (e.code !== 'Space' || spaceHeld || !pageWrapEl) return;
    const t = e.target as HTMLElement | null;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    spaceHeld = true;
    e.preventDefault();
  }
  function onPanKeyUp(e: KeyboardEvent): void {
    if (e.code === 'Space') spaceHeld = false;
  }
  function onPanPointerDown(e: PointerEvent): void {
    // Pan via middle mouse OR while Space is held (works with a touchpad — no middle button needed),
    // so ordinary text selection still works when Space isn't held.
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
    // If the reader is closed while still in zen (portaled to <body>), remove the orphaned node so
    // it can't linger over the app / block a clean re-open (2026-07-16).
    if (typeof document !== 'undefined' && readerEl?.parentNode === document.body) readerEl.remove();
    zenAnchor?.remove();
  });
</script>

<svelte:window on:keydown={onPanKeyDown} on:keyup={onPanKeyUp} />

<section
  class="reader"
  class:zen
  class:mode-dim={readingMode === 'dim'}
  class:mode-dark={readingMode === 'dark'}
  bind:this={readerEl}
>
  <header>
    <div>
      <h3>{fileName}</h3>
      <span>{fileId.slice(0, 8)}</span>
    </div>
    <nav aria-label="Reader panels">
      <button type="button" class:active={tab === 'pdf'} on:click={() => (tab = 'pdf')}
        title="Show the PDF pages">Paper</button>
      <button type="button" class:active={tab === 'contexts'} on:click={() => (tab = 'contexts')}
        title="Show extracted in-text citations and their references">
        References
      </button>
      <button
        type="button"
        class:active={tab === 'annotations'}
        on:click={() => (tab = 'annotations')}
        title="Show and add notes and highlights"
      >
        Notes
      </button>
    </nav>
  </header>

  {#if readerStatus}
    <p class="reader-status" class:error={readerStatusError} role="status">{readerStatus}</p>
  {/if}

  {#if tab === 'pdf'}
    {#if !fileUrl}
      <p class="empty">Open a paper in the reader</p>
    {:else}
      <div class="pdf-toolbar">
        <div class="pager">
          <button type="button" on:click={() => goTo(currentPage - 1)} disabled={currentPage <= 1}
            title={currentPage <= 1 ? 'Already on the first page' : 'Previous page'}>
            ‹
          </button>
          <span>{currentPage} / {numPages || '?'}</span>
          <button
            type="button"
            on:click={() => goTo(currentPage + 1)}
            disabled={currentPage >= numPages}
            title={currentPage >= numPages ? 'Already on the last page' : 'Next page'}
          >
            ›
          </button>
        </div>
        <div class="zoom">
          <button type="button" on:click={() => zoom(-0.2)} aria-label="Zoom out" title="Zoom out">−</button>
          <span>{Math.round(scale * 100)}%</span>
          <button type="button" on:click={() => zoom(0.2)} aria-label="Zoom in" title="Zoom in">+</button>
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
        <div class="mode" role="group" aria-label="Page reading mode" data-testid="reading-mode">
          <span class="mode-label">Page:</span>
          {#each READING_MODE_OPTIONS as opt (opt.value)}
            <button
              type="button"
              class:active={readingMode === opt.value}
              on:click={() => setReadingMode(opt.value)}
              data-testid={`reading-mode-${opt.value}`}
              title={opt.title}
            >
              {opt.label}
            </button>
          {/each}
        </div>
        <form class="search" on:submit|preventDefault={runSearch}>
          <input
            bind:value={searchQuery}
            placeholder="Search whole paper…"
            title="In-app search scans the whole paper. Browser Ctrl+F searches only the visible page."
          />
          <button type="submit" disabled={searching || !searchQuery.trim()}
            title={searchQuery.trim() ? 'Search the whole paper' : 'Type a phrase to search for'}>Find</button>
          {#if searchHits.length}
            <button type="button" on:click={() => stepSearch(-1)} aria-label="Previous match"
              title="Previous match">‹</button
            >
            <span>{searchPos + 1}/{searchHits.length}</span>
            <button type="button" on:click={() => stepSearch(1)} aria-label="Next match" title="Next match">›</button>
          {:else if usedServerFallback && searchQuery.trim()}
            <!-- Scanned/OCR'd PDF: no in-browser text layer to highlight, so report the count
                 of matches found in the server-extracted (OCR) text instead. -->
            <span title="This looks like a scanned PDF. Matches are counted in the OCR-extracted text; on-page highlighting isn't available.">
              {serverSearchCount} in text ({serverTextSource})
            </span>
          {/if}
        </form>
        <button
          type="button"
          class="copy-text-btn"
          on:click={copyExtractedText}
          disabled={serverTextLoading || !onFetchText}
          title="Copy the paper's full extracted text (uses OCR for scanned PDFs)"
        >
          {serverTextLoading ? 'Extracting…' : copyStatus || 'Copy text'}
        </button>
        <button
          type="button"
          class="select-btn"
          on:click={captureSelection}
          disabled={!canAnnotate}
          title={canAnnotate
            ? 'Capture the selected text as a highlight annotation'
            : INSUFFICIENT_ROLE}
        >
          Highlight selection
        </button>
        <button
          type="button"
          class="zen-btn"
          class:active={zen}
          on:click={toggleZen}
          data-testid="reader-zen"
          title={zen
            ? 'Leave zen mode (Esc)'
            : 'Zen mode: the reader fills the screen over a dark backdrop; only paging, zoom and page-mode controls stay'}
        >
          {zen ? 'Exit zen' : 'Zen'}
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
              title="Go to page {i + 1}"
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
          style={`--page-filter:${pageFilter}`}
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
              {#each citationBoxesForPage(contexts, currentPage) as item (item.context.id + ':' + item.box.x + ',' + item.box.y)}
                <button
                  type="button"
                  class="overlay"
                  class:flash={flashKey === `ctx:${item.context.id}`}
                  title={item.context.reference_title ??
                    item.context.marker_text ??
                    'citation'}
                  on:click={() => onOverlayClick(item.context)}
                  style={boxToStyle(item.box, scale)}
                ></button>
              {/each}
              {#each annotationBoxesForPage(annotations, currentPage) as item (item.annotation.id + ':' + item.box.x + ',' + item.box.y)}
                <div
                  class="annotation-overlay"
                  class:flash={flashKey === `ann:${item.annotation.id}`}
                  title={item.annotation.content_markdown ?? item.annotation.selected_text ?? "annotation"}
                  style={boxToStyle(item.box, scale)}
                ></div>
              {/each}
            </div>
          {:else}
            <div class="scroll-stack">
              {#each Array(numPages) as _, i (i)}
                <div
                  class="canvas-stage scroll-page"
                  style={`--scale-factor:${scale};${
                    basePageHeight
                      ? `min-height:${Math.round(basePageHeight * scale)}px;width:${Math.round(basePageWidth * scale)}px;`
                      : ''
                  }`}
                  use:registerScrollPage={i + 1}
                >
                  <canvas use:bindScrollCanvas={i + 1}></canvas>
                  <div class="textLayer" use:bindScrollLayer={i + 1}></div>
                  {#each citationBoxesForPage(contexts, i + 1) as item (item.context.id + ':' + item.box.x + ',' + item.box.y)}
                    <button
                      type="button"
                      class="overlay"
                      class:flash={flashKey === `ctx:${item.context.id}`}
                      title={item.context.reference_title ?? item.context.marker_text ?? 'citation'}
                      on:click={() => onOverlayClick(item.context)}
                      style={boxToStyle(item.box, scale)}
                    ></button>
                  {/each}
                  {#each annotationBoxesForPage(annotations, i + 1) as item (item.annotation.id + ':' + item.box.x + ',' + item.box.y)}
                    <div
                      class="annotation-overlay"
                      class:flash={flashKey === `ann:${item.annotation.id}`}
                      title={item.annotation.content_markdown ?? item.annotation.selected_text ?? "annotation"}
                      style={boxToStyle(item.box, scale)}
                    ></div>
                  {/each}
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
          <article id={`reader-ctx-${context.id}`} class:flash={flashKey === `ctx:${context.id}`}>
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
                <button type="button" class="jump" on:click={() => jumpToContext(context)}
                  title="Jump to where this citation appears in the paper">
                  Jump to p.{context.pdf_coordinates?.[0]?.page ?? context.page}
                </button>
              {/if}
              {#if context.reference_id && onNavigateToReference}
                <button
                  type="button"
                  class="jump"
                  on:click={() => onNavigateToReference?.(context.reference_id)}
                  title="Reveal the matching entry in the paper’s References list"
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
      <select bind:value={annotationType} disabled={!canAnnotate || !onCreateAnnotation}
        title={canAnnotate ? 'Choose the kind of annotation to add' : INSUFFICIENT_ROLE}>
        <option value="note">Note</option>
        <option value="highlight">Highlight</option>
        <option value="page_anchor">Page anchor</option>
        <option value="citation_note">Citation note</option>
      </select>
      <input bind:value={annotationPage} inputmode="numeric" placeholder="Page" disabled={!canAnnotate}
        title={canAnnotate ? 'Page this note refers to' : INSUFFICIENT_ROLE} />
      <input bind:value={selectedText} placeholder="Selected text" disabled={!canAnnotate}
        title={canAnnotate ? 'Text this note refers to (filled in by Highlight selection)' : INSUFFICIENT_ROLE} />
      <textarea bind:value={annotationContent} placeholder="Note" disabled={!canAnnotate}
        title={canAnnotate ? 'Your note text' : INSUFFICIENT_ROLE}></textarea>
      {#if selectionBoxes?.length}
        <small class="coord-note">
          Anchored at p.{selectionBoxes[0].page} ({selectionBoxes.length} box{selectionBoxes.length >
          1
            ? 'es'
            : ''} from selection)
        </small>
      {/if}
      <button
        type="submit"
        disabled={!canAnnotate || !onCreateAnnotation || (!selectedText && !annotationContent)}
        title={canAnnotate ? 'Save this note/highlight' : INSUFFICIENT_ROLE}
      >
        Add
      </button>
      {#if !canAnnotate}<small class="role-hint">{INSUFFICIENT_ROLE} — reading is read-only.</small>{/if}
    </form>

    {#if annotations.length === 0}
      <p class="empty">No annotations</p>
    {:else}
      <div class="annotation-list">
        {#each annotations as annotation (annotation.id)}
          <article class:flash={flashKey === `ann:${annotation.id}`}>
            <header>
              <button type="button" class="ann-jump" on:click={() => jumpToAnnotation(annotation)}
                title="Jump to this note’s page and highlight">
                <strong>{annotation.annotation_type.replaceAll('_', ' ')}</strong>
                <span>page {annotation.page ?? '-'}</span>
              </button>
              {#if onDeleteAnnotation}
                <button
                  type="button"
                  class="ann-delete"
                  aria-label="Delete annotation"
                  title={canAnnotate ? 'Delete this note' : INSUFFICIENT_ROLE}
                  disabled={!canAnnotate}
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

  /* 2026-07-16: Dim/Dark also ease the reader CHROME (toolbar, panels, page-wrap), not just the page
     canvas. Dark gives dark-on-light reader UI; Dim warms it (cream/amber). Scoped to the reader —
     the rest of the app keeps its own theme. On an already-dark app theme these just deepen slightly,
     which reads fine. */
  .reader.mode-dark {
    --reader-chrome-bg: #14161a;
    --reader-chrome-fg: #cdd3dc;
    --reader-chrome-border: #2a2f37;
    background: #0f1114;
    color: var(--reader-chrome-fg);
    border-radius: 6px;
    padding: 0.4rem;
  }
  .reader.mode-dim {
    --reader-chrome-bg: #f6efdd;
    --reader-chrome-fg: #4a3f2a;
    --reader-chrome-border: #e4d9bd;
    background: #fbf6e9;
    color: var(--reader-chrome-fg);
    border-radius: 6px;
    padding: 0.4rem;
  }
  .reader.mode-dark .pdf-toolbar,
  .reader.mode-dim .pdf-toolbar,
  .reader.mode-dark header,
  .reader.mode-dim header {
    color: var(--reader-chrome-fg);
  }
  .reader.mode-dark .page-wrap,
  .reader.mode-dark .thumbs,
  .reader.mode-dim .page-wrap,
  .reader.mode-dim .thumbs {
    background: var(--reader-chrome-bg);
    border-color: var(--reader-chrome-border);
  }
  .reader.mode-dark .reader-status,
  .reader.mode-dim .reader-status {
    color: var(--reader-chrome-fg);
  }

  .reader-status {
    margin: 0;
    font-size: 0.85rem;
    color: var(--ink-muted);
  }
  .reader-status.error {
    color: var(--status-error, #c0392b);
    font-weight: 600;
  }

  /* Zen mode (UX batch 3): fill the viewport over a dark backdrop — the app never shows through.
     Only the paging/zoom/view-mode/reading-mode controls (and Exit zen) remain visible. */
  .reader.zen {
    align-content: start;
    background: #101216;
    inset: 0;
    /* Force full-viewport coverage: without an explicit width/max-width, an inherited max-width on
       .reader left the fixed overlay narrower than the screen, so the paper-view behind it peeked at
       the left/right edges (2026-07-16). */
    width: 100vw;
    max-width: none;
    margin: 0;
    overflow: auto;
    padding: 0.7rem 1rem 1rem;
    position: fixed;
    z-index: 1000; /* above the paper-view modal backdrop */
  }

  .reader.zen header,
  .reader.zen .search,
  .reader.zen .copy-text-btn,
  .reader.zen .select-btn,
  .reader.zen .search-hint {
    display: none;
  }

  .reader.zen .pdf-toolbar {
    color: #cdd3dc;
  }

  .reader.zen .zen-btn {
    margin-left: auto;
  }

  .reader.zen .page-wrap,
  .reader.zen .thumbs {
    background: #14161a;
    border-color: #262a31;
    max-height: calc(100vh - 5.8rem);
  }

  .zen-btn.active {
    background: var(--accent-primary);
    color: var(--ink-inverse);
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
    color: var(--ink-strong);
    font-size: 1rem;
    line-height: 1.2;
    margin: 0;
    overflow-wrap: anywhere;
  }

  span,
  small,
  .empty {
    color: var(--ink-muted);
  }

  span {
    font-size: 0.78rem;
  }

  nav {
    display: flex;
    gap: 0.35rem;
  }

  button {
    background: var(--surface-overlay);
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    color: var(--ink-strong);
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
    border: 1px solid var(--border-strong);
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
    background: var(--accent-primary);
    color: var(--ink-inverse);
  }

  .pdf-toolbar {
    align-items: center;
    background: var(--surface-sunken);
    border: 1px solid var(--border-strong);
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

  .mode-label {
    color: var(--ink-muted);
    font-size: 0.78rem;
    font-weight: 700;
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
    color: var(--ink-muted);
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
    background: var(--surface-hover);
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    display: flex;
    /* `safe center`: centre the page while it fits, but when zoomed wider than the viewport fall
       back to start-alignment so the LEFT edge stays reachable by scrolling (plain `center` clips
       the overflow on the start side — you could only ever scroll to reveal the right; 2026-07-16). */
    justify-content: safe center;
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
    /* Same safe-centering as .page-wrap for the horizontal (cross) axis when zoomed pages overflow. */
    align-items: safe center;
    gap: 0.6rem;
  }

  .canvas-stage {
    position: relative;
  }

  /* Reading-mode easing targets the page canvas ONLY, so the sibling text-selection layer and the
     highlight / citation / annotation overlays keep their true colours. */
  .canvas-stage canvas {
    display: block;
    filter: var(--page-filter, none);
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
    /* 2026-07-16: hoverable so the native title tooltip (the stored note) shows over the highlight.
       `pointer-events:none` previously suppressed it; making it interactive blocks text selection
       only over the small highlighted region, which is fine (that text is already noted). */
    pointer-events: auto;
    cursor: help;
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
    color: var(--status-danger);
  }

  .coord-note,
  .role-hint {
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
    background: var(--surface-hover);
    border: 1px solid var(--border-strong);
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
    border-color: var(--status-danger-border);
    color: var(--status-danger);
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
