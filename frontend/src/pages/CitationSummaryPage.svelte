<script lang="ts">
  import {
    ApiClient,
    type CitationSummary,
    type ExternalPreview,
    type GraphScopeType,
    type MissingDecision,
    type MissingWork,
    type RankedWork,
    type VenueAuthorSummary,
    type Work,
    type YearCount,
  } from '../api/client';
  import Modal from '../components/Modal.svelte';
  import ScopePicker from '../components/ScopePicker.svelte';
  import WorkDetail from '../components/WorkDetail.svelte';
  import { buildChronologicalOption } from '../lib/viz/citationSummary';
  import ChartHost from '../components/ChartHost.svelte';
  import { activeVizTheme } from '../lib/theme/store';
  import { resolveScopeRequest, type ScopeRequest } from '../lib/scope';
  import { pendingLibraryOpen, pendingLibrarySearch, selectedPaperIds } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;
  // Whether the tab is visible (#9): ECharts mis-sizes when built while display:none.
  export let visible = true;

  // Scope state, bound into the shared ScopePicker (C3); readiness is computed there.
  let scopeType: GraphScopeType = 'library';
  let scopeId = '';
  let searchQuery = '';
  let batchId = '';
  let savedFilterId = '';
  let scopeReady = true;

  let summary: CitationSummary | null = null;
  let busy = false;
  let message = '';
  let importing = '';

  // Sub-tabs (batch10 #7): Overview (the citation analytics) + Venues + Authors aggregations.
  // The venue/author aggregation is fetched lazily on first sub-tab open (C4) — most visits stay
  // on Overview, so the summary build no longer pays for an aggregation nobody looks at.
  let subtab: 'overview' | 'venues' | 'authors' = 'overview';
  let venueAuthor: VenueAuthorSummary | null = null;
  let venueAuthorBusy = false;

  // A paper opened directly in the paper view (WorkDetail modal) — the same action the search tab
  // uses (C2). Distinct from the title click, which jumps to the Library tab.
  let detailWork: Work | null = null;

  // Frequently-cited-but-missing worklist (C3a): key -> 'import' | 'ignore'. Loaded with the summary.
  let decisions: Record<string, MissingDecision> = {};
  let showIgnored = false;

  // External-reference previews (C1): key -> preview | 'loading' | null (fetched on demand).
  let previews: Record<string, ExternalPreview | 'loading'> = {};

  let exportingMissing = false;

  // Inline "open in a panel/view" glyph for the paper-view icon button (kept self-contained).
  const openIcon =
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" ' +
    'stroke-width="1.5" aria-hidden="true"><rect x="2" y="2.5" width="12" height="11" rx="1.5"/>' +
    '<path d="M2 5.5h12"/><path d="M5.5 9l2 2 3.5-4"/></svg>';

  let chartRevision = 0;

  async function load(): Promise<void> {
    if (!scopeReady) return;
    busy = true;
    message = '';
    try {
      const scopeArgs = await resolveScopeRequest(
        client,
        { scopeType, scopeId, searchQuery, batchId, savedFilterId },
        $selectedPaperIds,
      );
      currentScopeArgs = scopeArgs;
      summary = await client.citationSummary(scopeArgs);
      previews = {};
      decisions = await client.getWorklist();
      // The venue/author aggregation belongs to the old scope now — refetch immediately only when
      // the user is already looking at it, otherwise drop it and let the sub-tab lazy-load.
      venueAuthor = null;
      if (subtab !== 'overview') await loadVenueAuthor();
    } catch (error) {
      message = errorMessage(error);
      summary = null;
    } finally {
      busy = false;
    }
  }

  async function loadVenueAuthor(): Promise<void> {
    if (!currentScopeArgs || venueAuthorBusy) return;
    venueAuthorBusy = true;
    try {
      venueAuthor = await client.venueAuthorSummary(currentScopeArgs);
    } catch (error) {
      message = errorMessage(error);
    } finally {
      venueAuthorBusy = false;
    }
  }

  function openSubtab(tab: typeof subtab): void {
    subtab = tab;
    if (tab !== 'overview' && !venueAuthor) void loadVenueAuthor();
  }

  // The resolved scope arguments used for the last summary, reused for the missing-list exports.
  let currentScopeArgs: ScopeRequest | null = null;

  function openPaper(workId: string): void {
    pendingLibraryOpen.set(workId);
    if (typeof window !== 'undefined') window.location.hash = '#library';
  }

  // Venue/author rows jump to a Library metadata search via the venue:/author: operators. Authors
  // search by family name (the grouped display name is "Family, I." — the initial-only form would
  // miss "First Family" spellings; the family-name substring matches both).
  function searchLibrary(query: string): void {
    pendingLibrarySearch.set({ query, mode: 'metadata' });
    if (typeof window !== 'undefined') window.location.hash = '#library';
  }

  function searchVenue(name: string): void {
    searchLibrary(`venue:"${name.replace(/"/g, '')}"`);
  }

  function searchAuthor(name: string): void {
    const family = (name.includes(',') ? name.split(',')[0] : name).trim();
    searchLibrary(`author:"${family.replace(/"/g, '')}"`);
  }

  // Open the paper directly in the in-app paper view (WorkDetail modal), reusing the search-tab flow.
  async function openInPaperView(workId: string): Promise<void> {
    try {
      detailWork = await client.getWork(workId);
    } catch (error) {
      message = errorMessage(error);
    }
  }

  async function importMissing(missing: MissingWork): Promise<void> {
    if (!missing.reference_id) return;
    importing = missing.key;
    try {
      const work = await client.importReferenceAsWork(missing.reference_id);
      // Refresh so the imported work leaves the "missing" list; then open it.
      await load();
      openPaper(work.id);
    } catch (error) {
      message = errorMessage(error);
    } finally {
      importing = '';
    }
  }

  async function togglePreview(missing: MissingWork): Promise<void> {
    if (missing.key in previews) {
      const { [missing.key]: _drop, ...rest } = previews;
      previews = rest;
      return;
    }
    previews = { ...previews, [missing.key]: 'loading' };
    try {
      const preview = await client.externalPreview({
        doi: missing.doi,
        arxiv: missing.arxiv_id,
        referenceId: missing.reference_id,
      });
      previews = { ...previews, [missing.key]: preview };
    } catch (error) {
      message = errorMessage(error);
      const { [missing.key]: _drop, ...rest } = previews;
      previews = rest;
    }
  }

  async function decide(missing: MissingWork, decision: MissingDecision): Promise<void> {
    try {
      decisions = await client.setWorklistDecision(missing.key, decision);
    } catch (error) {
      message = errorMessage(error);
    }
  }

  async function undoDecision(missing: MissingWork): Promise<void> {
    try {
      decisions = await client.clearWorklistDecision(missing.key);
    } catch (error) {
      message = errorMessage(error);
    }
  }

  async function exportMissing(format: 'bibtex' | 'csv'): Promise<void> {
    exportingMissing = true;
    try {
      const result = await client.exportMissingWorks({
        ...(currentScopeArgs ?? { scopeType }),
        format,
      });
      const url = URL.createObjectURL(new Blob([result.content], { type: result.content_type }));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = result.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      message = errorMessage(error);
    } finally {
      exportingMissing = false;
    }
  }

  // Partition the missing list by decision so ignored items collapse out of the active worklist.
  $: activeMissing = (summary?.frequently_cited_missing ?? []).filter(
    (m) => decisions[m.key] !== 'ignore',
  );
  $: ignoredMissing = (summary?.frequently_cited_missing ?? []).filter(
    (m) => decisions[m.key] === 'ignore',
  );

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function renderChart(chart: any): void {
    if (!summary || summary.chronological.length === 0) return;
    chart.setOption(buildChronologicalOption(summary, $activeVizTheme), true);
  }

  // Clicking a year bar opens a popup listing that year's papers (clickable titles).
  let yearPopup: YearCount | null = null;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function wireChartEvents(chart: any): void {
    chart.on('click', (params: { name?: string }) => {
      if (!params.name || !summary) return;
      yearPopup =
        summary.chronological.find(
          (entry) => (entry.year === null ? 'Unknown' : String(entry.year)) === params.name,
        ) ?? null;
    });
  }

  $: if (summary) chartRevision += 1;
</script>

<section class="layout">
  {#if message}<p class="msg" role="status">{message}</p>{/if}

  <div class="card">
    <h2>Citation summary</h2>
    <p class="muted">
      Scoped citation analytics (§8.11): the most-cited papers in and beyond your library, the papers
      you cite most but don't have, bridge and isolated papers, and how your scope spreads over time.
    </p>

    <div class="controls">
      <ScopePicker
        {client}
        bind:scopeType
        bind:scopeId
        bind:searchQuery
        bind:batchId
        bind:savedFilterId
        bind:ready={scopeReady}
        verb="summarize"
        testid="summary"
      />

      <button type="button" on:click={load} disabled={busy || !scopeReady} data-testid="summary-build">
        {summary ? 'Refresh' : 'Summarize'}
      </button>
    </div>
  </div>

  {#if summary}
    <div class="card">
      <div class="subtabs" role="tablist">
        <button type="button" role="tab" aria-selected={subtab === 'overview'}
          class:active={subtab === 'overview'} on:click={() => openSubtab('overview')}
          data-testid="cs-tab-overview">Overview</button>
        <button type="button" role="tab" aria-selected={subtab === 'venues'}
          class:active={subtab === 'venues'} on:click={() => openSubtab('venues')}
          data-testid="cs-tab-venues">Venues{#if venueAuthor} ({venueAuthor.distinct_venue_count}){/if}</button>
        <button type="button" role="tab" aria-selected={subtab === 'authors'}
          class:active={subtab === 'authors'} on:click={() => openSubtab('authors')}
          data-testid="cs-tab-authors">Authors{#if venueAuthor} ({venueAuthor.distinct_author_count}){/if}</button>
      </div>

      {#if subtab === 'overview'}
      {#if summary.notes.length > 0}
        <ul class="notes" data-testid="summary-notes">
          {#each summary.notes as note (note)}<li>{note}</li>{/each}
        </ul>
      {/if}
      <p class="meta" data-testid="summary-meta">
        {summary.scope_work_count} papers · bridge method: {summary.bridge_method}
      </p>

      {#if summary.coverage_total > 0}
        <div class="coverage" data-testid="summary-coverage">
          <span class="coverage-pct">{summary.coverage_pct}%</span>
          <span class="coverage-text">
            You hold <strong>{summary.coverage_pct}%</strong> of the works your library cites
            ({summary.coverage_held} / {summary.coverage_total}).
          </span>
          <span class="coverage-bar" aria-hidden="true">
            <span class="coverage-fill" style="width:{summary.coverage_pct}%"></span>
          </span>
        </div>
      {/if}

      <div class="grid">
        <div class="block" data-testid="summary-most-cited-local">
          <h3>Most-cited (in your library)</h3>
          {#if summary.most_cited_local.length === 0}
            <p class="empty">No local citations in this scope.</p>
          {:else}
            <ol>
              {#each summary.most_cited_local as w (w.work_id)}
                <li>
                  <button class="link" type="button" on:click={() => openPaper(w.work_id)}>{w.title}</button>
                  <span class="badge">{w.score} citing</span>
                  <button
                    class="iconbtn"
                    type="button"
                    title="Open in paper view"
                    aria-label="Open in paper view"
                    data-testid="open-paper-view"
                    on:click={() => openInPaperView(w.work_id)}>{@html openIcon}</button>
                </li>
              {/each}
            </ol>
          {/if}
        </div>

        <div class="block" data-testid="summary-most-cited-external">
          <h3>Most-cited (external impact)</h3>
          {#if summary.most_cited_external.length === 0}
            <p class="empty">No citation counts fetched for this scope yet.</p>
          {:else}
            <ol>
              {#each summary.most_cited_external as w (w.work_id)}
                <li>
                  <button class="link" type="button" on:click={() => openPaper(w.work_id)}>{w.title}</button>
                  <span class="badge">{w.score} citations</span>
                  <button
                    class="iconbtn"
                    type="button"
                    title="Open in paper view"
                    aria-label="Open in paper view"
                    data-testid="open-paper-view"
                    on:click={() => openInPaperView(w.work_id)}>{@html openIcon}</button>
                </li>
              {/each}
            </ol>
          {/if}
        </div>

        <div class="block missing-block" data-testid="summary-missing">
          <div class="block-head">
            <h3>Frequently cited but missing</h3>
            {#if summary.frequently_cited_missing.length > 0}
              <span class="export-group">
                <button
                  class="ghost"
                  type="button"
                  disabled={exportingMissing}
                  on:click={() => exportMissing('bibtex')}
                  data-testid="summary-export-bibtex"
                  title="Export the missing list as BibTeX">BibTeX</button>
                <button
                  class="ghost"
                  type="button"
                  disabled={exportingMissing}
                  on:click={() => exportMissing('csv')}
                  data-testid="summary-export-csv"
                  title="Export the missing list as CSV">CSV</button>
              </span>
            {/if}
          </div>
          {#if summary.frequently_cited_missing.length === 0}
            <p class="empty">Every frequently-cited work is already in your library.</p>
          {:else}
            <ol data-testid="summary-missing-active">
              {#each activeMissing as m (m.key)}
                <li>
                  <div class="missing-row">
                    <span class="missing-title">{m.title}</span>
                    <span class="badge">{m.cited_by_count} cite this</span>
                    {#if decisions[m.key] === 'import'}
                      <span class="badge queued" data-testid="summary-queued">queued</span>
                    {/if}
                  </div>
                  <div class="missing-actions">
                    {#if m.doi || m.arxiv_id || m.reference_id}
                      <button
                        class="ghost"
                        type="button"
                        on:click={() => togglePreview(m)}
                        data-testid="summary-preview-toggle"
                        title="Preview title, authors and abstract before importing">
                        {m.key in previews ? 'Hide preview' : 'Preview'}
                      </button>
                    {/if}
                    {#if decisions[m.key] === 'import'}
                      <button
                        class="ghost"
                        type="button"
                        on:click={() => undoDecision(m)}
                        data-testid="summary-undo">Un-queue</button>
                    {:else}
                      <button
                        class="ghost"
                        type="button"
                        on:click={() => decide(m, 'import')}
                        data-testid="summary-queue"
                        title="Mark this work as one to acquire">Queue</button>
                    {/if}
                    <button
                      class="ghost"
                      type="button"
                      on:click={() => decide(m, 'ignore')}
                      data-testid="summary-ignore"
                      title="Hide this work from the active list">Ignore</button>
                    {#if m.reference_id}
                      <button
                        class="import"
                        type="button"
                        disabled={importing === m.key}
                        on:click={() => importMissing(m)}
                        data-testid="summary-import">
                        {importing === m.key ? 'Importing…' : 'Import'}
                      </button>
                    {/if}
                  </div>
                  {#if m.key in previews}
                    <div class="preview" data-testid="summary-preview">
                      {#if previews[m.key] === 'loading'}
                        <p class="empty">Loading preview…</p>
                      {:else if previews[m.key] && (previews[m.key] as ExternalPreview).available}
                        {@const p = previews[m.key] as ExternalPreview}
                        <p class="preview-title">{p.title ?? m.title}</p>
                        {#if p.authors.length}<p class="preview-meta">{p.authors.join(', ')}</p>{/if}
                        <p class="preview-meta">
                          {[p.year, p.venue].filter(Boolean).join(' · ')}
                        </p>
                        {#if p.abstract}<p class="preview-abstract">{p.abstract}</p>{/if}
                        {#if p.sources.length}
                          <p class="preview-src">via {p.sources.join(', ')}</p>
                        {/if}
                      {:else}
                        <p class="empty">
                          {(previews[m.key] as ExternalPreview)?.message ?? 'No preview available.'}
                        </p>
                      {/if}
                    </div>
                  {/if}
                </li>
              {/each}
            </ol>
            {#if ignoredMissing.length > 0}
              <details class="ignored" bind:open={showIgnored} data-testid="summary-ignored">
                <summary>Ignored ({ignoredMissing.length})</summary>
                <ol>
                  {#each ignoredMissing as m (m.key)}
                    <li>
                      <span class="missing-title muted-strike">{m.title}</span>
                      <button
                        class="ghost"
                        type="button"
                        on:click={() => undoDecision(m)}
                        data-testid="summary-undo">Restore</button>
                    </li>
                  {/each}
                </ol>
              </details>
            {/if}
          {/if}
        </div>

        <div class="block" data-testid="summary-bridge">
          <h3>Bridge papers</h3>
          {#if summary.bridge_papers.length === 0}
            <p class="empty">No bridge papers detected.</p>
          {:else}
            <ol>
              {#each summary.bridge_papers as w (w.work_id)}
                <li>
                  <button class="link" type="button" on:click={() => openPaper(w.work_id)}>{w.title}</button>
                  <span class="badge">centrality {w.score}</span>
                  <button
                    class="iconbtn"
                    type="button"
                    title="Open in paper view"
                    aria-label="Open in paper view"
                    data-testid="open-paper-view"
                    on:click={() => openInPaperView(w.work_id)}>{@html openIcon}</button>
                </li>
              {/each}
            </ol>
          {/if}
        </div>

        <div class="block" data-testid="summary-isolated">
          <h3>Isolated papers</h3>
          {#if summary.isolated_papers.length === 0}
            <p class="empty">Every paper connects to the rest of the scope.</p>
          {:else}
            <ol>
              {#each summary.isolated_papers as w (w.work_id)}
                <li>
                  <button class="link" type="button" on:click={() => openPaper(w.work_id)}>{w.title}</button>
                  <button
                    class="iconbtn"
                    type="button"
                    title="Open in paper view"
                    aria-label="Open in paper view"
                    data-testid="open-paper-view"
                    on:click={() => openInPaperView(w.work_id)}>{@html openIcon}</button>
                </li>
              {/each}
            </ol>
          {/if}
        </div>

        <div class="block chrono" data-testid="summary-chronological">
          <h3>Papers by year</h3>
          {#if summary.chronological.length === 0}
            <p class="empty">No dated papers in this scope.</p>
          {:else}
            <div class="chart" data-testid="summary-chart">
              <ChartHost render={renderChart} onReady={wireChartEvents} revision={chartRevision}
                {visible} height="100%" ariaLabel="Chronological citation chart" />
            </div>
          {/if}
        </div>
      </div>
      {:else if subtab === 'venues'}
        {#if venueAuthor}
          <p class="meta">
            {venueAuthor.distinct_venue_count} distinct venue(s) across {venueAuthor.scope_work_count}
            paper(s){#if venueAuthor.papers_without_venue}; {venueAuthor.papers_without_venue} without a
            recorded venue{/if}.
          </p>
          {#if venueAuthor.venues.length === 0}
            <p class="empty">No venues recorded for these papers.</p>
          {:else}
            <table class="stat-table" data-testid="venue-table">
              <thead><tr><th>Venue</th><th class="num">Papers</th><th class="num">Share</th><th>Years</th></tr></thead>
              <tbody>
                {#each venueAuthor.venues as v (v.name)}
                  <tr>
                    <td>
                      <button class="link" type="button" on:click={() => searchVenue(v.name)}
                        title="Show this venue's papers in the Library">{v.name}</button>
                      {#if v.variants.length > 1}
                        <span class="variants" title={`Merged spellings: ${v.variants.join(', ')}`}
                          >+{v.variants.length - 1} spelling(s)</span>
                      {/if}
                    </td>
                    <td class="num">{v.count}</td>
                    <td class="num">{v.pct}%</td>
                    <td>{v.year_min ? (v.year_min === v.year_max ? v.year_min : `${v.year_min}–${v.year_max}`) : '—'}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
            <p class="hintline">Venues are grouped case- and punctuation-insensitively; abbreviations
              and full names are not merged.</p>
          {/if}
        {:else}
          <p class="empty">Loading venue statistics…</p>
        {/if}
      {:else if subtab === 'authors'}
        {#if venueAuthor}
          <p class="meta">
            {venueAuthor.distinct_author_count} distinct author(s) across {venueAuthor.scope_work_count}
            paper(s){#if venueAuthor.papers_without_authors}; {venueAuthor.papers_without_authors} without
            recorded authors{/if}.
          </p>
          {#if venueAuthor.authors.length === 0}
            <p class="empty">No authors recorded for these papers.</p>
          {:else}
            <table class="stat-table" data-testid="author-table">
              <thead><tr><th>Author</th><th class="num">Papers</th><th class="num">Share</th></tr></thead>
              <tbody>
                {#each venueAuthor.authors as a (a.name)}
                  <tr>
                    <td>
                      <button class="link" type="button" on:click={() => searchAuthor(a.name)}
                        title="Show this author's papers in the Library">{a.name}</button>
                      {#if a.variants.length > 1}
                        <span class="variants" title={`Merged name forms: ${a.variants.join(', ')}`}
                          >+{a.variants.length - 1} form(s)</span>
                      {/if}
                    </td>
                    <td class="num">{a.count}</td>
                    <td class="num">{a.pct}%</td>
                  </tr>
                {/each}
              </tbody>
            </table>
            <p class="hintline">Authors are grouped by last name + first initial (so "Vaswani, A." and
              "Ashish Vaswani" count once).</p>
          {/if}
        {:else}
          <p class="empty">Loading author statistics…</p>
        {/if}
      {/if}
    </div>
  {/if}
</section>

{#if yearPopup}
  <Modal
    title={`Papers ${yearPopup.year === null ? 'with unknown year' : `from ${yearPopup.year}`} (${yearPopup.work_count})`}
    onClose={() => (yearPopup = null)}
  >
    <ol class="year-popup-list" data-testid="year-popup">
      {#each yearPopup.works as w (w.work_id)}
        <li>
          <button class="link" type="button" title="Open in the Library tab"
            on:click={() => { yearPopup = null; openPaper(w.work_id); }}>{w.title}</button>
          <button
            class="iconbtn"
            type="button"
            title="Open in paper view"
            aria-label="Open in paper view"
            on:click={() => { yearPopup = null; void openInPaperView(w.work_id); }}>{@html openIcon}</button>
        </li>
      {/each}
    </ol>
  </Modal>
{/if}

{#if detailWork}
  <Modal title={detailWork.title ?? 'Paper'} wide onClose={() => (detailWork = null)}>
    {#key detailWork.id}
      <WorkDetail
        {client}
        work={detailWork}
        onUpdated={(w) => (detailWork = w)}
        onDeleted={() => (detailWork = null)}
        onClose={() => (detailWork = null)}
      />
    {/key}
  </Modal>
{/if}

<style>
  .layout {
    display: grid;
    gap: 1rem;
  }

  .card {
    background: var(--surface-raised);
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    padding: 1rem;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0 0 0.4rem;
  }

  h3 {
    font-size: 0.9rem;
    margin: 0 0 0.4rem;
  }

  .muted,
  .hintline,
  .meta {
    color: var(--ink-muted);
    font-size: 0.85rem;
  }

  .meta {
    margin: 0 0 0.8rem;
  }

  .subtabs {
    border-bottom: 1px solid var(--border-normal);
    display: flex;
    gap: 0.25rem;
    margin: 0 0 0.8rem;
  }

  .subtabs button {
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    color: var(--ink-muted);
    cursor: pointer;
    font: inherit;
    padding: 0.4rem 0.7rem;
  }

  .subtabs button.active {
    border-bottom-color: var(--accent, var(--status-info));
    color: var(--ink-normal);
    font-weight: 600;
  }

  .stat-table {
    border-collapse: collapse;
    width: 100%;
  }

  .stat-table th,
  .stat-table td {
    border-bottom: 1px solid var(--border-normal);
    padding: 0.4rem 0.5rem;
    text-align: left;
  }

  .stat-table th {
    color: var(--ink-muted);
    font-size: 0.75rem;
    text-transform: uppercase;
  }

  .stat-table .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  .variants {
    color: var(--ink-muted);
    font-size: 0.75rem;
    margin-left: 0.35rem;
  }

  .controls {
    align-items: flex-end;
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    margin-top: 0.6rem;
  }

  button {
    background: var(--accent-primary);
    border: 1px solid var(--accent-primary);
    border-radius: 6px;
    color: var(--ink-inverse);
    cursor: pointer;
    font: inherit;
    font-weight: 700;
    min-height: 2.1rem;
    padding: 0.35rem 0.8rem;
  }

  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .grid {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr));
  }

  .block ol {
    margin: 0;
    padding-left: 1.2rem;
  }

  .block li {
    margin: 0.25rem 0;
  }

  .link {
    background: none;
    border: none;
    color: var(--status-info);
    cursor: pointer;
    font: inherit;
    min-height: 0;
    padding: 0;
    text-align: left;
    text-decoration: underline;
  }

  .missing-title {
    color: var(--ink-strong);
  }

  .badge {
    color: var(--ink-muted);
    font-size: 0.78rem;
    margin-left: 0.3rem;
  }

  .import {
    background: var(--status-success);
    border-color: var(--status-success);
    font-size: 0.75rem;
    margin-left: 0.4rem;
    min-height: 0;
    padding: 0.1rem 0.45rem;
  }

  .coverage {
    align-items: center;
    background: var(--surface-sunken, var(--surface-raised));
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    margin: 0 0 0.9rem;
    padding: 0.6rem 0.9rem;
  }

  .coverage-pct {
    color: var(--accent-primary);
    font-size: 1.5rem;
    font-weight: 800;
  }

  .coverage-text {
    color: var(--ink-strong);
    font-size: 0.9rem;
  }

  .coverage-bar {
    background: var(--border-strong);
    border-radius: 999px;
    flex: 1 1 8rem;
    height: 0.5rem;
    overflow: hidden;
  }

  .coverage-fill {
    background: var(--accent-primary);
    display: block;
    height: 100%;
  }

  .iconbtn {
    align-items: center;
    background: none;
    border: none;
    color: var(--status-info);
    cursor: pointer;
    display: inline-flex;
    margin-left: 0.35rem;
    min-height: 0;
    padding: 0.1rem;
    vertical-align: middle;
  }

  .ghost {
    background: none;
    border: 1px solid var(--border-strong);
    color: var(--ink-strong);
    font-size: 0.72rem;
    font-weight: 600;
    min-height: 0;
    padding: 0.1rem 0.4rem;
  }

  .block-head {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
  }

  .export-group {
    display: inline-flex;
    gap: 0.3rem;
  }

  .missing-block li {
    margin-bottom: 0.6rem;
  }

  .missing-row {
    align-items: baseline;
    display: flex;
    flex-wrap: wrap;
    gap: 0.2rem;
  }

  .missing-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    margin-top: 0.2rem;
  }

  .queued {
    color: var(--status-success);
    font-weight: 700;
  }

  .preview {
    background: var(--surface-raised);
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    margin-top: 0.35rem;
    padding: 0.5rem 0.7rem;
  }

  .preview-title {
    color: var(--ink-strong);
    font-weight: 700;
    margin: 0 0 0.2rem;
  }

  .preview-meta {
    color: var(--ink-muted);
    font-size: 0.82rem;
    margin: 0 0 0.2rem;
  }

  .preview-abstract {
    color: var(--ink-strong);
    font-size: 0.84rem;
    margin: 0.3rem 0 0;
    max-height: 9rem;
    overflow-y: auto;
  }

  .preview-src {
    color: var(--ink-muted);
    font-size: 0.72rem;
    margin: 0.3rem 0 0;
  }

  .ignored {
    margin-top: 0.6rem;
  }

  .ignored summary {
    color: var(--ink-muted);
    cursor: pointer;
    font-size: 0.82rem;
  }

  .muted-strike {
    color: var(--ink-muted);
    text-decoration: line-through;
  }

  .chart {
    height: 16rem;
    width: 100%;
  }

  .year-popup-list {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    margin: 0;
    padding-left: 1.2rem;
  }

  .notes {
    background: var(--status-warning-bg);
    border-radius: 6px;
    color: var(--status-warning);
    font-size: 0.82rem;
    list-style: none;
    margin: 0 0 0.6rem;
    padding: 0.4rem 0.7rem;
  }

  .empty {
    color: var(--ink-muted);
    font-size: 0.85rem;
  }
</style>
