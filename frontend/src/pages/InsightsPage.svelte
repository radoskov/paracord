<!-- InsightsPage — scope-level analysis: citation/topic graph, topic modeling, and an
     LLM-generated scope summary, plus a free-form per-scope note. Props: client (ApiClient),
     visible (forwarded to CitationGraph, which mis-sizes while hidden).
     Non-obvious: large scopes are answered as a queued background job (awaitAnalysis/pollJob poll
     until done); scope notes and the cached scope summary each reactively reload whenever the
     resolved scope (or, for the summary, the chosen detail level) changes; "Summarize" always
     forces recomputation of the scope-level synthesis while optionally also regenerating the
     underlying per-paper summaries (regeneratePapers). -->
<script lang="ts">
  import {
    ApiClient,
    type CitationGraphResponse,
    type GraphNodeMode,
    type GraphScopeType,
    type ScopeNote,
    type ScopeSummaryResponse,
    type SummaryDetail,
    type Topic,
  } from '../api/client';
  import CitationGraph from '../components/CitationGraph.svelte';
  import ExportDialog from '../components/ExportDialog.svelte';
  import ScopePicker from '../components/ScopePicker.svelte';
  import RecommendPanel from '../components/RecommendPanel.svelte';
  import { resolveScopeRequest } from '../lib/scope';
  import { renderSummaryMath } from '../lib/renderMath';
  import { pendingLibraryOpen, pendingLibrarySearch, selectedPaperIds, pendingIdentifierImport, pendingImportText } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;
  // Whether the Insights tab is visible (#9). Forwarded to CitationGraph so it can resize the
  // chart after the tab is shown again (it mis-sizes while hidden).
  export let visible = true;

  // Sub-tabs within Insights (like the Import tab): the analysis views, and AI recommendations.
  let activeSubTab: 'analysis' | 'recommend' = 'analysis';

  // Scope state, bound into the shared ScopePicker (C3); readiness is computed there.
  let scopeType: GraphScopeType = 'library';
  let scopeId = '';
  let searchQuery = '';
  let batchId = '';
  let savedFilterId = '';
  let scopeReady = true;

  let topics: Topic[] = [];
  let summary: ScopeSummaryResponse | null = null;
  let loading = false;
  let message = '';

  $: scope = {
    scopeType,
    scopeId: scopeType === 'library' ? null : scopeId || null,
  };

  // The classic scopes (library/shelf/rack) drive Topics, Summary and Export; the Phase B6 graph
  // scopes only apply to the citation graph.
  $: isClassicScope =
    scopeType === 'library' || scopeType === 'shelf' || scopeType === 'rack';

  // 2026-07-16 scope notes: a free-form note for the selected scope, plus a folded panel of all
  // scope notes. Loaded on scope change; saved on demand.
  let scopeNote = '';
  let scopeNoteSaving = false;
  let scopeNoteSavedAt = '';
  let allNotes: ScopeNote[] = [];
  let notesExpanded = false;

  async function loadScopeNote(): Promise<void> {
    if (!scopeReady || !isClassicScope) {
      scopeNote = '';
      return;
    }
    try {
      const n = await client.getScopeNote(
        scope.scopeType as 'library' | 'shelf' | 'rack',
        scope.scopeId,
      );
      scopeNote = n.text ?? '';
      scopeNoteSavedAt = n.updated_at ? new Date(n.updated_at).toLocaleString() : '';
    } catch {
      scopeNote = '';
      scopeNoteSavedAt = '';
    }
  }
  // Reload the note whenever the selected scope changes.
  $: void (scope.scopeType, scope.scopeId, scopeReady, isClassicScope, loadScopeNote());

  async function saveScopeNote(): Promise<void> {
    if (!isClassicScope) return;
    scopeNoteSaving = true;
    await run(async () => {
      const n = await client.upsertScopeNote(
        scope.scopeType as 'library' | 'shelf' | 'rack',
        scope.scopeId,
        scopeNote,
      );
      scopeNoteSavedAt = n.updated_at ? new Date(n.updated_at).toLocaleString() : '';
      if (notesExpanded) allNotes = await client.listScopeNotes();
    }, 'Note saved');
    scopeNoteSaving = false;
  }

  async function onNotesToggle(): Promise<void> {
    if (notesExpanded) {
      try {
        allNotes = await client.listScopeNotes();
      } catch {
        allNotes = [];
      }
    }
  }

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

  // Item 1 (2026-07-13): cap on external (cited-but-not-in-library) nodes; server default 50.
  let maxExternal = 50;

  // L-a: a large scope answers {queued, job_id}; poll the job and fetch the stored result so
  // callers (the graph components) transparently get the final payload either way.
  async function awaitAnalysis<T extends { queued?: boolean; job_id?: string | null }>(
    response: T,
  ): Promise<T> {
    if (!response.queued || !response.job_id) return response;
    message = 'Large scope — computing on the server in the background…';
    for (let attempt = 0; attempt < 900; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const out = await client.getJobResult(response.job_id);
      if (out.status === 'finished') {
        message = '';
        return out.result as T;
      }
      if (out.status === 'failed' || out.status === 'missing' || out.status === 'unavailable') {
        throw new Error(out.error ?? `Background computation ${out.status}`);
      }
    }
    throw new Error('Background computation timed out — see the Jobs tab');
  }

  async function loadGraph(
    nodeMode: GraphNodeMode,
    collapseVersions: boolean,
    colorBy: import('../api/client').GraphColorBy,
  ): Promise<CitationGraphResponse> {
    // resolveScopeRequest runs a search_result search now and passes the ids as the explicit work
    // set; a saved_filter id is expanded + visibility-clamped by the backend.
    const scopeArgs = await resolveScopeRequest(
      client,
      { scopeType, scopeId, searchQuery, batchId, savedFilterId },
      $selectedPaperIds,
    );
    return awaitAnalysis(
      await client.citationGraph({ maxExternal, ...scopeArgs, nodeMode, collapseVersions, colorBy }),
    );
  }

  // Topic (embedding-similarity) graph over the current scope (#6). Same scope resolution, so
  // both graph types share the same scope picker.
  async function loadTopicGraph(): Promise<import('../api/client').TopicGraphResponse> {
    const scopeArgs = await resolveScopeRequest(
      client,
      { scopeType, scopeId, searchQuery, batchId, savedFilterId },
      $selectedPaperIds,
    );
    return awaitAnalysis(await client.topicGraph(scopeArgs));
  }

  // External graph node → prefill the Import tab's Identifier form with its DOI so one click
  // lands on "import by DOI" (previously this jumped to a Library search, which can't import).
  function importExternal(doi: string): void {
    pendingIdentifierImport.set(doi);
    if (typeof window !== 'undefined') window.location.hash = '#import';
  }

  // A DOI-less external/citing node has no identifier to import by → prefill the Import tab's
  // Citations box with the metadata we do have (a free-text "Title (year)" line); the Import tab
  // auto-switches to the Citations sub-tab when this store is set.
  function importCitation(line: string): void {
    pendingImportText.set(line);
    if (typeof window !== 'undefined') window.location.hash = '#import';
  }

  // S15: a large scope is answered with a queued background job — poll until it leaves the
  // active set, then run the completion callback (refresh the relevant view).
  async function pollJob(jobId: string, onDone: () => Promise<void>): Promise<void> {
    const active = new Set(['queued', 'started', 'deferred', 'scheduled']);
    for (let attempt = 0; attempt < 150; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      try {
        const status = await client.getJobs(100);
        const job = status.jobs.find((j) => j.id === jobId);
        if (job && job.status === 'failed') {
          message = `Background job failed: ${job.error ?? 'see the Jobs tab'}`;
          return;
        }
        if (!job || !active.has(job.status)) {
          await onDone();
          return;
        }
      } catch {
        // transient polling error — keep trying
      }
    }
    message = 'Still running in the background — check the Jobs tab.';
  }

  // Busy flags (UX batch 4): the buttons show a running state (and can't be re-clicked) for the
  // whole run, INCLUDING the background-job wait — previously a queued job looked like nothing
  // had happened.
  let topicsBusy = false;
  let summaryBusy = false;
  let maxTopics = 8;

  async function modelTopics(): Promise<void> {
    topicsBusy = true;
    let backgrounded = false;
    await run(async () => {
      const response = await client.modelTopics({
        scopeType: scope.scopeType,
        scopeId: scope.scopeId,
        maxTopics,
      });
      if (response.queued && response.job_id) {
        backgrounded = true;
        message = `Modeling ${response.work_count} papers in the background…`;
        void pollJob(response.job_id, async () => {
          // UX batch 4: fetch the stored result — a whole-library run used to end with a
          // "refresh the graph" note and NO topics.
          const latest = await client.getLatestTopics(scope.scopeType, scope.scopeId);
          topics = latest.topics;
          outlierIds = latest.outlier_work_ids ?? [];
          expandedTopic = null;
          outliersExpanded = false;
          message = `Modeled ${latest.topics.length} topics over ${latest.work_count} papers`;
        }).finally(() => (topicsBusy = false));
        return;
      }
      topics = response.topics;
      outlierIds = response.outlier_work_ids ?? [];
      expandedTopic = null;
      outliersExpanded = false;
      message = `Modeled ${response.topics.length} topics over ${response.work_count} papers`;
    });
    if (!backgrounded) topicsBusy = false;
  }

  // C4: the modeler always returns per-topic representatives + coherence and scope-level outliers;
  // they used to be fetched and dropped. Titles are looked up lazily on first expand.
  let outlierIds: string[] = [];
  let expandedTopic: number | null = null;
  let outliersExpanded = false;
  let workTitles: Record<string, string> = {};

  async function ensureTitles(ids: string[]): Promise<void> {
    const missing = ids.filter((id) => !(id in workTitles));
    const fetched = await Promise.all(
      missing.map(async (id) => {
        try {
          const work = await client.getWork(id);
          return [id, work.title || 'Untitled paper'] as const;
        } catch {
          return [id, 'Paper unavailable'] as const;
        }
      }),
    );
    if (fetched.length) workTitles = { ...workTitles, ...Object.fromEntries(fetched) };
  }

  async function toggleTopicExamples(topic: Topic): Promise<void> {
    expandedTopic = expandedTopic === topic.topic_id ? null : topic.topic_id;
    if (expandedTopic !== null) await ensureTitles(topic.representative_work_ids);
  }

  async function toggleOutliers(): Promise<void> {
    outliersExpanded = !outliersExpanded;
    if (outliersExpanded) await ensureTitles(outlierIds);
  }

  function openPaper(workId: string): void {
    pendingLibraryOpen.set(workId);
    if (typeof window !== 'undefined') window.location.hash = '#library';
  }

  // Prettify the scope summary (UX batch 4): the LLM emits numbered sections often with
  // "Heading: - bullet" runs on one line. Parse that into heading paragraphs + bullet lists so it
  // renders as structured sections instead of one run-on block. Falls back to plain paragraphs.
  type SummarySection = { heading?: string; body?: string; bullets?: string[] };
  // 2026-07-16 no-PDF honesty: footer breakdown of how the scope's papers were summarized.
  function sourceBreakdownLabel(b: { full_text: number; abstract_only: number; title_only: number }): string {
    const parts: string[] = [];
    if (b.full_text) parts.push(`${b.full_text} with PDFs`);
    if (b.abstract_only) parts.push(`${b.abstract_only} abstract-only`);
    if (b.title_only) parts.push(`${b.title_only} title-only`);
    return parts.join(', ');
  }

  function fmtGenerated(iso: string): string {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString();
  }

  function formatSummary(text: string): SummarySection[] {
    const out: SummarySection[] = [];
    // Break before a leading number ("2. Key problems…") or a known section label mid-run.
    const normalized = text
      .replace(/\s+(\d\.\s)/g, '\n$1')
      .replace(/\s+-\s+/g, '\n- ');
    for (const rawLine of normalized.split('\n')) {
      const line = rawLine.trim();
      if (!line) continue;
      if (line.startsWith('- ')) {
        const item = line.slice(2).trim();
        const last = out[out.length - 1];
        if (last?.bullets) last.bullets.push(item);
        else out.push({ bullets: [item] });
        continue;
      }
      // "1. Heading: rest" or "Heading:" — split a leading heading from any inline body.
      const num = line.match(/^(\d\.\s*)?(.*)$/);
      const rest = num ? num[2] : line;
      const colon = rest.indexOf(':');
      if (colon > 0 && colon < 40) {
        out.push({ heading: rest.slice(0, colon + 1) });
        const body = rest.slice(colon + 1).trim();
        if (body) out.push({ body });
      } else {
        out.push({ body: rest });
      }
    }
    return out.length ? out : [{ body: text }];
  }

  // UX batch 4: which per-paper summary feeds the scope synthesis, and whether to reuse or
  // regenerate them — maps 1:1 to the backend paper_detail/regenerate_papers.
  let summarySource:
    | 'use_short'
    | 'use_detailed'
    | 'regen_short'
    | 'regen_detailed' = 'use_short';

  // 2026-07-16: when a "detailed" source is picked, the effort level (fast/section/deep) is chosen
  // via radios below the dropdown; short has a single level.
  const SCOPE_EFFORTS: { value: SummaryDetail; label: string; hint: string }[] = [
    { value: 'detailed_fast', label: 'Fast', hint: 'Group sections into ~4 buckets (cheapest)' },
    { value: 'detailed_section', label: 'Section', hint: 'One pass per top-level section' },
    { value: 'detailed_deep', label: 'Deep', hint: 'One pass per subsection (most detail, slowest)' },
  ];
  let scopeEffort: SummaryDetail = 'detailed_section';
  // 2026-07-16: render summary maths with KaTeX ("fancy") or raw text ("plain" fallback).
  let mathMode: 'fancy' | 'plain' = 'fancy';
  $: summaryDetail = (summarySource.endsWith('detailed') ? scopeEffort : 'short') as SummaryDetail;

  async function summarise(): Promise<void> {
    summaryBusy = true;
    let backgrounded = false;
    // 2026-07-16: clicking the button ALWAYS re-synthesizes the scope summary (force=true) — even
    // "Use/create short → Regenerate" now recomputes, picking up new papers/PDFs (their per-paper
    // summaries are created on the fly) while REUSING existing per-paper summaries. Only the
    // "Regenerate short/detailed" options set regeneratePapers, which additionally re-does the
    // existing per-paper summaries. (The cached summary is shown read-only via loadCachedSummary;
    // this button is the explicit recompute.)
    const regeneratePapers = summarySource.startsWith('regen');
    await run(async () => {
      const response = await client.createScopeScope(scope.scopeType, scope.scopeId, {
        paperDetail: summaryDetail,
        regeneratePapers,
        force: true,
      });
      if (response.queued && response.job_id) {
        backgrounded = true;
        message = `Summarizing ${response.work_count} papers in the background…`;
        void pollJob(response.job_id, async () => {
          summary = await client.getLatestScopeSummary(scope.scopeType, scope.scopeId, summaryDetail);
          message = 'Summary ready.';
        }).finally(() => (summaryBusy = false));
        return;
      }
      summary = response;
    }, 'Summary generated');
    if (!backgrounded) summaryBusy = false;
  }

  // Show the cached summary for the selected (scope, effort) if one exists, so the button reads
  // "Regenerate" and the footer shows its provenance — else the Generate interface (2026-07-16).
  async function loadCachedSummary(): Promise<void> {
    if (!scopeReady || !isClassicScope || summaryBusy) return;
    try {
      summary = await client.getLatestScopeSummary(scope.scopeType, scope.scopeId, summaryDetail);
    } catch {
      summary = null; // 404 → nothing cached for this cell yet
    }
  }
  // Re-fetch when the scope or the selected effort/source changes.
  $: void (scope.scopeType, scope.scopeId, summaryDetail, loadCachedSummary());

</script>

<section class="layout">
  <nav class="insights-tabs" aria-label="Insights sections">
    <button type="button" class="insights-tab" class:active={activeSubTab === 'analysis'}
      on:click={() => (activeSubTab = 'analysis')} data-testid="insights-subtab-analysis">Analysis</button>
    <button type="button" class="insights-tab" class:active={activeSubTab === 'recommend'}
      on:click={() => (activeSubTab = 'recommend')} data-testid="insights-subtab-recommend">Recommend categorization</button>
  </nav>

  {#if activeSubTab === 'recommend'}
    <div class="card">
      <h2>Recommend categorization</h2>
      <p class="muted">Let a model suggest tags, or rows/racks/shelves, for each paper in a scope
        from its title, abstract, keywords and topics. Review and accept per paper.</p>
      <RecommendPanel {client} />
    </div>
  {:else}
  {#if message}<p class="muted msg">{message}</p>{/if}

  <div class="card scope">
    <h2>Scope</h2>
    <p class="muted">The set of papers the graph, topics and summary below operate on.</p>
    <ScopePicker
      {client}
      bind:scopeType
      bind:scopeId
      bind:searchQuery
      bind:batchId
      bind:savedFilterId
      bind:ready={scopeReady}
      verb="analyze"
      testid="insights"
    >
      <label class="max-external-label"
        title="Keep only this many external (not-in-library) cited papers in graphs — the most-cited ones. In-library nodes are never hidden.">
        Max external
        <input type="number" min="0" max="500" bind:value={maxExternal}
          aria-label="Maximum external nodes" style="width:5rem" />
      </label>
    </ScopePicker>
  </div>

  <div class="card">
    <CitationGraph
      label={scopeType === 'library' ? '· whole library' : `· ${scopeType.replace('_', ' ')}`}
      disabled={loading || !scopeReady}
      load={loadGraph}
      loadTopic={loadTopicGraph}
      onOpenWork={openPaper}
      onImportExternal={importExternal}
      onImportCitation={importCitation}
      {visible}
    />
  </div>

  <div class="grid">
    <div class="card">
      <div class="head">
        <h2>Topics</h2>
        <label class="max-topics" title="How many topics to cluster the papers into (1-20)">
          Max
          <input type="number" min="1" max="20" bind:value={maxTopics} style="width:3.5rem" />
        </label>
        <button type="button" class:busy={topicsBusy} on:click={modelTopics}
          disabled={loading || topicsBusy || !scopeReady || !isClassicScope}
          title={isClassicScope ? (scopeReady ? 'Cluster the scope’s papers into keyword topics' : `Pick a ${scopeType} first`) : 'Topics work on a library, shelf or rack scope'}
          >{topicsBusy ? 'Modeling…' : 'Model topics'}</button>
      </div>
      {#if topics.length === 0}
        <p class="empty">No topics yet — click “Model topics”.</p>
      {:else}
        <ul class="plain">
          {#each topics as topic (topic.topic_id)}
            <li>
              <strong>{topic.keywords.join(', ')}</strong>
              <small class="muted"> · {topic.work_count} papers</small>
              {#if topic.coherence_score != null}
                <small class="muted" title="How tightly this topic’s papers cluster together (100% = near-identical)">
                  · {Math.round(topic.coherence_score * 100)}% coherent</small>
              {/if}
              {#if topic.works?.length}
                <button type="button" class="linkbtn" on:click={() => toggleTopicExamples(topic)}
                  title="List this topic’s papers (best fit first) — click one to open it">
                  {expandedTopic === topic.topic_id ? 'Hide papers' : 'Show papers'}
                </button>
                {#if expandedTopic === topic.topic_id}
                  <ul class="plain nested">
                    {#each topic.works as w (w.id)}
                      <li><button type="button" class="linkbtn" on:click={() => openPaper(w.id)}>{w.title ?? 'Untitled paper'}</button></li>
                    {/each}
                  </ul>
                {/if}
              {:else if topic.representative_work_ids.length > 0}
                <button type="button" class="linkbtn" on:click={() => toggleTopicExamples(topic)}
                  title="The papers closest to this topic’s center">
                  {expandedTopic === topic.topic_id ? 'Hide examples' : 'Examples'}
                </button>
                {#if expandedTopic === topic.topic_id}
                  <ul class="plain nested">
                    {#each topic.representative_work_ids as id (id)}
                      <li><button type="button" class="linkbtn" on:click={() => openPaper(id)}>{workTitles[id] ?? 'Loading…'}</button></li>
                    {/each}
                  </ul>
                {/if}
              {/if}
            </li>
          {/each}
        </ul>
        {#if outlierIds.length > 0}
          <p class="hint-toggle">
            <button type="button" class="linkbtn" on:click={toggleOutliers}
              title="Papers whose content matched none of the topics">
              {outlierIds.length} paper{outlierIds.length === 1 ? '' : 's'} fit no topic
              {outliersExpanded ? '· hide' : '· show'}
            </button>
          </p>
          {#if outliersExpanded}
            <ul class="plain nested">
              {#each outlierIds as id (id)}
                <li><button type="button" class="linkbtn" on:click={() => openPaper(id)}>{workTitles[id] ?? 'Loading…'}</button></li>
              {/each}
            </ul>
          {/if}
        {/if}
      {/if}
    </div>

    <div class="card">
      <div class="head">
        <h2>Scope summary</h2>
        <label class="summary-source" title="Which per-paper summaries feed the collection synthesis. Summarize/Regenerate always rebuilds the scope summary; 'Reuse' keeps existing per-paper summaries (only new/changed papers are summarized), 'Regenerate' redoes every per-paper summary too.">
          <select bind:value={summarySource} disabled={summaryBusy}>
            <option value="use_short">Reuse short (fill new)</option>
            <option value="use_detailed">Reuse detailed (fill new)</option>
            <option value="regen_short">Regenerate all short</option>
            <option value="regen_detailed">Regenerate all detailed</option>
          </select>
        </label>
        {#if summarySource.endsWith('detailed')}
          <div class="effort-radios" role="radiogroup" aria-label="Detailed effort">
            {#each SCOPE_EFFORTS as e}
              <label class="effort" class:active={scopeEffort === e.value} title={e.hint}>
                <input type="radio" name="scope-effort" value={e.value} bind:group={scopeEffort} disabled={summaryBusy} />
                {e.label}
              </label>
            {/each}
          </div>
        {/if}
        <button type="button" class:busy={summaryBusy} on:click={summarise}
          disabled={loading || summaryBusy || !scopeReady || !isClassicScope}
          title={isClassicScope ? (scopeReady ? 'Rebuild the scope summary now — new/changed papers get summarized, existing per-paper summaries are reused unless a "Regenerate all" source is chosen' : `Pick a ${scopeType} first`) : 'Summaries work on a library, shelf or rack scope'}
          >{summaryBusy ? 'Summarizing…' : summary ? 'Regenerate' : 'Summarize'}</button>
      </div>
      {#if !summary}
        <p class="empty">No summary yet — click “Summarize”.</p>
      {:else}
        {#if summary.scope_label}
          <p class="summary-scope" data-testid="summary-scope-label">
            Summary of <strong>{summary.scope_label}</strong>
          </p>
        {/if}
        <div class="summary-toolbar">
          <button type="button" class="linkish" class:active={mathMode === 'fancy'}
            on:click={() => (mathMode = mathMode === 'fancy' ? 'plain' : 'fancy')}
            title="Toggle LaTeX math rendering (switch to plain if equations look garbled)"
            >{mathMode === 'fancy' ? '𝑓𝑥 fancy' : 'plain text'}</button>
        </div>
        <div class="summary-body">
          {#each formatSummary(summary.text) as sec}
            {#if sec.heading}<p class="summary-heading">{sec.heading}</p>{/if}
            {#if sec.bullets}
              <ul class="summary-bullets">{#each sec.bullets as b}<li>{#if mathMode === 'fancy'}{@html renderSummaryMath(b)}{:else}{b}{/if}</li>{/each}</ul>
            {:else if mathMode === 'fancy'}
              <p class="summary-text">{@html renderSummaryMath(sec.body ?? '')}</p>
            {:else}
              <p class="summary-text">{sec.body}</p>
            {/if}
          {/each}
        </div>
        <p class="hintline">{summary.summary_type} · {summary.work_count} papers · {summary.model_name ?? 'local'}{summary.method === 'map_reduce' ? ' · per-paper digests synthesized' : ''}{summary.source_breakdown ? ` · ${sourceBreakdownLabel(summary.source_breakdown)}` : ''}{summary.generated_at ? ` · generated ${fmtGenerated(summary.generated_at)}` : ''}</p>
        {#if summary.provider_used !== 'local_llm'}
          <p class="extractive-hint" role="status">
            {#if summary.fallback && summary.fallback_reason}
              Extractive summary — the configured AI model was unavailable ({summary.fallback_reason}).
            {:else}
              Extractive summary — set an AI summary model in Admin → AI to enable model-based summaries.
            {/if}
          </p>
        {/if}
      {/if}
    </div>
  </div>

  <!-- 2026-07-16: a free-form note for the selected scope, saved permanently. -->
  {#if scopeReady && isClassicScope}
    <div class="card">
      <div class="head">
        <h2>Notes</h2>
        {#if scopeNoteSavedAt}<span class="muted">saved {scopeNoteSavedAt}</span>{/if}
      </div>
      <textarea class="scope-note" bind:value={scopeNote} rows="4"
        placeholder={`Your notes for ${scope.scopeType === 'library' ? 'the whole library' : 'this ' + scope.scopeType} (saved with the scope)`}
      ></textarea>
      <div class="note-actions">
        <button type="button" class:busy={scopeNoteSaving} on:click={saveScopeNote}
          disabled={loading || scopeNoteSaving}>{scopeNoteSaving ? 'Saving…' : 'Save note'}</button>
      </div>
    </div>
  {/if}

  <!-- Export lives at the bottom, folded (UX batch 3): occasionally useful, shouldn't sit between
       the scope picker and the graph. -->
  {#if scopeReady && (isClassicScope || scopeType === 'saved_filter')}
    <details class="card export-block">
      <summary>Export this {scopeType === 'saved_filter' ? 'saved filter' : scopeType}</summary>
      {#if isClassicScope}
        <ExportDialog
          label={`this ${scopeType}`}
          fetchExport={(format, style) =>
            client.exportCitations({
              scope_type: scopeType,
              scope_id: scopeId || undefined,
              format,
              style,
            })}
          fetchStyles={() => client.listCitationStyles()}
        />
      {:else}
        <ExportDialog
          label="this saved filter"
          fetchExport={(format, style) =>
            client.exportCitations({
              scope_type: 'saved_filter',
              scope_id: savedFilterId || undefined,
              format,
              style,
            })}
          fetchStyles={() => client.listCitationStyles()}
        />
      {/if}
    </details>
  {/if}

  <!-- 2026-07-16: all scope notes, folded — each headed by its scope type/parameters. -->
  <details class="card notes-block" bind:open={notesExpanded} on:toggle={onNotesToggle}>
    <summary>All scope notes</summary>
    {#if allNotes.length === 0}
      <p class="empty">No scope notes yet.</p>
    {:else}
      <ul class="all-notes">
        {#each allNotes as n (n.scope_type + ':' + (n.scope_id ?? 'library'))}
          <li>
            <p class="note-head">
              <strong>{n.scope_type}</strong>{#if n.scope_label} · {n.scope_label}{/if}
              {#if n.updated_at}<span class="muted"> · {new Date(n.updated_at).toLocaleString()}</span>{/if}
            </p>
            <p class="note-text">{n.text}</p>
          </li>
        {/each}
      </ul>
    {/if}
  </details>
  {/if}
</section>

<style>
  .layout {
    display: grid;
    gap: 1rem;
  }

  .insights-tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }

  .insights-tab {
    background: var(--surface-overlay);
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    color: var(--accent-secondary);
    font-weight: 600;
  }

  .insights-tab.active {
    background: var(--accent-primary);
    border-color: var(--accent-primary);
    color: var(--ink-inverse);
  }

  /* Running-state buttons (UX batch 4): visibly "working" while the job runs. */
  button.busy {
    background: var(--status-warning-bg);
    color: var(--status-warning);
    cursor: progress;
  }

  .max-topics {
    align-items: center;
    display: flex;
    font-size: 0.85rem;
    gap: 0.3rem;
  }

  .summary-scope {
    margin: 0 0 0.4rem;
  }

  .summary-source select {
    font-size: 0.85rem;
  }

  .scope-note {
    box-sizing: border-box;
    resize: vertical;
    width: 100%;
  }
  .note-actions {
    display: flex;
    justify-content: flex-end;
    margin-top: 0.4rem;
  }
  .notes-block summary {
    cursor: pointer;
    font-weight: 600;
  }
  .all-notes {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
  }
  .all-notes .note-head {
    margin: 0 0 0.15rem;
    text-transform: capitalize;
  }
  .all-notes .note-text {
    margin: 0;
    white-space: pre-wrap;
  }

  .summary-toolbar {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 0.2rem;
  }
  .summary-toolbar .active {
    font-weight: 700;
  }

  /* 2026-07-16 detailed-summary effort selector */
  .effort-radios {
    display: inline-flex;
    gap: 0.15rem;
    border: 1px solid var(--border);
    border-radius: 0.375rem;
    overflow: hidden;
  }
  .effort-radios .effort {
    font-size: 0.78rem;
    padding: 0.12rem 0.45rem;
    cursor: pointer;
    user-select: none;
  }
  .effort-radios .effort.active {
    background: var(--accent-soft, rgba(120, 120, 255, 0.18));
    font-weight: 600;
  }
  .effort-radios .effort input {
    display: none;
  }

  .summary-heading {
    font-weight: 600;
    margin: 0.6rem 0 0.2rem;
  }

  .summary-bullets {
    margin: 0.1rem 0 0.3rem;
    padding-left: 1.2rem;
  }

  .summary-body .summary-text {
    margin: 0.2rem 0;
  }

  .export-block summary {
    cursor: pointer;
    font-weight: 600;
  }

  .msg {
    margin: 0;
  }

  .extractive-hint {
    margin: 0.5rem 0 0;
    padding: 0.4rem 0.6rem;
    border-radius: 0.375rem;
    background: var(--status-warning-bg);
    color: var(--status-warning);
    font-size: 0.85rem;
  }

  .grid {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  }

  h2 {
    font-size: 1.05rem;
    margin: 0 0 0.5rem;
  }

  .head {
    align-items: center;
    display: flex;
    justify-content: space-between;
  }

  .head h2 {
    margin: 0;
  }

  .max-external-label {
    color: var(--ink-strong);
    display: flex;
    flex-direction: column;
    font-size: 0.8rem;
    font-weight: 700;
    gap: 0.2rem;
  }

  .plain {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
  }

  .plain.nested {
    border-left: 2px solid var(--border-strong);
    gap: 0.15rem;
    margin: 0.25rem 0 0.25rem 0.4rem;
    padding-left: 0.6rem;
  }

  .linkbtn {
    background: none;
    border: none;
    color: var(--status-info);
    cursor: pointer;
    font: inherit;
    font-size: 0.85rem;
    min-height: 0;
    padding: 0;
    text-align: left;
    text-decoration: underline;
  }

  .hint-toggle {
    margin: 0.4rem 0 0;
  }

  .summary-text {
    line-height: 1.5;
    margin: 0.3rem 0;
  }
</style>
