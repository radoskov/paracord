<script lang="ts">
  import {
    ApiClient,
    type CitationGraphResponse,
    type GraphNodeMode,
    type GraphScopeType,
    type ScopeSummaryResponse,
    type Topic,
  } from '../api/client';
  import CitationGraph from '../components/CitationGraph.svelte';
  import ExportDialog from '../components/ExportDialog.svelte';
  import ScopePicker from '../components/ScopePicker.svelte';
  import { resolveScopeRequest } from '../lib/scope';
  import { pendingLibraryOpen, pendingLibrarySearch, selectedPaperIds } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;
  // Whether the Insights tab is visible (#9). Forwarded to CitationGraph so it can resize the
  // chart after the tab is shown again (it mis-sizes while hidden).
  export let visible = true;

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

  // External graph node → jump to the Library search for its DOI so the user can import it (#8).
  function importExternal(doi: string): void {
    pendingLibrarySearch.set({ query: doi, mode: 'metadata' });
    if (typeof window !== 'undefined') window.location.hash = '#library';
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

  async function modelTopics(): Promise<void> {
    await run(async () => {
      const response = await client.modelTopics({
        scopeType: scope.scopeType,
        scopeId: scope.scopeId,
        maxTopics: 6,
      });
      if (response.queued && response.job_id) {
        message = `Modeling ${response.work_count} papers in the background — the topic graph will refresh when done.`;
        void pollJob(response.job_id, async () => {
          message = 'Topic model finished — refresh the topic graph to see the new topics.';
        });
        return;
      }
      topics = response.topics;
      outlierIds = response.outlier_work_ids ?? [];
      expandedTopic = null;
      outliersExpanded = false;
      message = `Modeled ${response.topics.length} topics over ${response.work_count} papers`;
    });
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

  async function summarise(): Promise<void> {
    await run(async () => {
      const response = await client.createScopeScope(scope.scopeType, scope.scopeId);
      if (response.queued && response.job_id) {
        message = `Summarizing ${response.work_count} papers in the background…`;
        void pollJob(response.job_id, async () => {
          summary = await client.getLatestScopeSummary(scope.scopeType, scope.scopeId);
          message = 'Summary ready.';
        });
        return;
      }
      summary = response;
    }, 'Summary generated');
  }

</script>

<section class="layout">
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
    {#if scopeReady && isClassicScope}
      <div style="margin-top:0.6rem">
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
      </div>
    {:else if scopeReady && scopeType === 'saved_filter'}
      <div style="margin-top:0.6rem">
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
      </div>
    {/if}
  </div>

  <div class="card">
    <CitationGraph
      label={scopeType === 'library' ? '· whole library' : `· ${scopeType.replace('_', ' ')}`}
      disabled={loading || !scopeReady}
      load={loadGraph}
      loadTopic={loadTopicGraph}
      onImportExternal={importExternal}
      {visible}
    />
  </div>

  <div class="grid">
    <div class="card">
      <div class="head">
        <h2>Topics</h2>
        <button type="button" on:click={modelTopics} disabled={loading || !scopeReady || !isClassicScope}
          title={isClassicScope ? (scopeReady ? 'Cluster the scope’s papers into keyword topics' : `Pick a ${scopeType} first`) : 'Topics work on a library, shelf or rack scope'}>Model topics</button>
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
              {#if topic.representative_work_ids.length > 0}
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
        <button type="button" on:click={summarise} disabled={loading || !scopeReady || !isClassicScope}
          title={isClassicScope ? (scopeReady ? 'Summarize the scope’s abstracts (uses the configured AI model when set, else extractive)' : `Pick a ${scopeType} first`) : 'Summaries work on a library, shelf or rack scope'}>Summarize</button>
      </div>
      {#if !summary}
        <p class="empty">No summary yet — click “Summarize”.</p>
      {:else}
        <p class="summary-text">{summary.text}</p>
        <p class="hintline">{summary.summary_type} · {summary.work_count} papers · {summary.model_name ?? 'local'}</p>
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
</section>

<style>
  .layout {
    display: grid;
    gap: 1rem;
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
