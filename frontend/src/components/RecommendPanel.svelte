<!-- RecommendPanel — the Insights "Recommend categorization" sub-tab. Pick a scope + pre-run options,
     run an AI recommendation (background job, polled via the cached run's status), then review and
     accept per-paper suggestions: tags (checkboxes → addTagLink) or categories (top-K shelves by
     combined score → addWorkToShelf), with two per-paper popups (raw scores; raw LLM I/O). -->
<script lang="ts">
  import { onDestroy } from 'svelte';

  import type { ApiClient, RecommendPaper, RecommendRun } from '../api/client';
  import Modal from './Modal.svelte';
  import ScopePicker from './ScopePicker.svelte';
  import { ensureShelves, shelves } from '../lib/catalog';
  import { emptyScopeSelection, resolveScopeRequest, type ScopeSelection } from '../lib/scope';
  import { selectedPaperIds } from '../lib/selection';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  // Scope state (same fields ScopePicker binds elsewhere in Insights).
  let sel: ScopeSelection = emptyScopeSelection();
  let scopeReady = true;

  // Pre-run options (workplan C5/C6/C8).
  let mode: 'tags' | 'categorization' = 'categorization';
  let k = 5;
  let scoring: 'ranking' | 'affinity' = 'ranking';
  let parentCombine: 'sum' | 'median' | 'max' = 'sum';
  let prefilter = false;

  let run: RecommendRun | null = null;
  let busy = false;
  let message = '';
  let poller: ReturnType<typeof setInterval> | null = null;

  // Per-paper acceptance selections: work_id → set of chosen tag_ids / shelf_ids.
  let chosenTags: Record<string, Set<string>> = {};
  let chosenShelves: Record<string, Set<string>> = {};
  let accepted: Record<string, string> = {}; // work_id → "N added" note

  // Popups.
  let rawScoresPaper: RecommendPaper | null = null;
  let rawLlmPaper: RecommendPaper | null = null;

  onDestroy(() => poller && clearInterval(poller));

  function stopPolling(): void {
    if (poller) {
      clearInterval(poller);
      poller = null;
    }
  }

  async function start(recompute = false): Promise<void> {
    if (!scopeReady) return;
    busy = true;
    message = '';
    stopPolling();
    chosenTags = {};
    chosenShelves = {};
    accepted = {};
    try {
      await ensureShelves(client);
      const scope = await resolveScopeRequest(client, sel, $selectedPaperIds);
      run = await client.createRecommendation({
        scopeType: scope.scopeType,
        scopeId: scope.scopeId ?? null,
        workIds: scope.workIds ?? null,
        mode,
        k,
        scoring,
        parentCombine,
        prefilter,
        recompute,
      });
      if (run.status === 'running') poll(run.id);
    } catch (error) {
      message = errorMessage(error);
    } finally {
      busy = false;
    }
  }

  function poll(runId: string): void {
    poller = setInterval(async () => {
      try {
        run = await client.getRecommendation(runId);
        if (run.status !== 'running') stopPolling();
      } catch (error) {
        message = errorMessage(error);
        stopPolling();
      }
    }, 2000);
  }

  $: papers = run?.result?.papers ?? [];
  $: capped = (run?.params?.capped as boolean) ?? false;
  $: totalInScope = (run?.params?.total_in_scope as number) ?? 0;
  function shelfDescription(shelfId: string): string {
    return $shelves.find((s) => s.id === shelfId)?.description ?? '';
  }

  function toggle(map: Record<string, Set<string>>, workId: string, id: string): Record<string, Set<string>> {
    const set = new Set(map[workId] ?? []);
    if (set.has(id)) set.delete(id);
    else set.add(id);
    return { ...map, [workId]: set };
  }

  async function acceptTags(paper: RecommendPaper): Promise<void> {
    const ids = [...(chosenTags[paper.work_id] ?? [])];
    if (!ids.length) return;
    try {
      for (const tagId of ids) await client.addTagLink(tagId, 'work', paper.work_id);
      accepted = { ...accepted, [paper.work_id]: `Added ${ids.length} tag${ids.length === 1 ? '' : 's'}` };
      chosenTags = { ...chosenTags, [paper.work_id]: new Set() };
    } catch (error) {
      message = errorMessage(error);
    }
  }

  async function acceptShelves(paper: RecommendPaper): Promise<void> {
    const ids = [...(chosenShelves[paper.work_id] ?? [])];
    if (!ids.length) return;
    try {
      for (const shelfId of ids) await client.addWorkToShelf(shelfId, paper.work_id);
      accepted = { ...accepted, [paper.work_id]: `Added to ${ids.length} shelf${ids.length === 1 ? '' : 'ves'}` };
      chosenShelves = { ...chosenShelves, [paper.work_id]: new Set() };
    } catch (error) {
      message = errorMessage(error);
    }
  }
</script>

<div class="recommend">
  <div class="controls">
    <ScopePicker {client} bind:scopeType={sel.scopeType} bind:scopeId={sel.scopeId}
      bind:searchQuery={sel.searchQuery} bind:batchId={sel.batchId} bind:savedFilterId={sel.savedFilterId}
      bind:ready={scopeReady} verb="recommend for" testid="recommend" />

    <div class="options">
      <label>Mode
        <select bind:value={mode} data-testid="rec-mode">
          <option value="categorization">Categorization (rows/racks/shelves)</option>
          <option value="tags">Tags</option>
        </select>
      </label>
      <label>Top K
        <input type="number" min="1" max="50" bind:value={k} data-testid="rec-k" class="num" />
      </label>
      <label title="Rank uses position points (K−p+1); Affinity uses the model's 0–100 score when it returns one">
        Scoring
        <select bind:value={scoring} data-testid="rec-scoring">
          <option value="ranking">Ranking (position points)</option>
          <option value="affinity">Affinity (model 0–100)</option>
        </select>
      </label>
      {#if mode === 'categorization'}
        <label title="How multiple parent rows/racks combine before the 0.5 boost">
          Combine parents
          <select bind:value={parentCombine} data-testid="rec-combine">
            <option value="sum">Sum</option>
            <option value="median">Median</option>
            <option value="max">Max</option>
          </select>
        </label>
      {/if}
      <label class="toggle" title="Pre-shortlist candidates by embedding similarity (faster, smaller prompts)">
        <input type="checkbox" bind:checked={prefilter} data-testid="rec-prefilter" /> Embedding pre-filter
      </label>
      <button type="button" on:click={() => start(false)} disabled={!scopeReady || busy}
        data-testid="rec-run">{busy ? 'Starting…' : 'Run'}</button>
      {#if run && run.status !== 'running'}
        <button type="button" class="secondary" on:click={() => start(true)} disabled={busy}
          title="Discard the cached result and recompute">Recompute</button>
      {/if}
    </div>
  </div>

  {#if message}<p class="msg" role="alert">{message}</p>{/if}

  {#if run}
    {#if run.status === 'running'}
      <p class="status" data-testid="rec-running">Computing… {(run.result?.papers?.length ?? 0)} done. This can take a while for large scopes — you can leave and come back (the result is cached).</p>
    {:else if run.status === 'failed'}
      <p class="msg" role="alert">Failed: {run.error}</p>
    {:else}
      <div class="run-meta">
        <span class="muted">Model: {run.provider_used ?? run.model_name} · {papers.length} paper{papers.length === 1 ? '' : 's'}{#if run.params?.updated_at}{/if} · computed {new Date(run.updated_at).toLocaleString()}</span>
        {#if capped}<span class="warn">Scope capped to {papers.length} of {totalInScope} papers.</span>{/if}
        {#if run.fallback}
          <span class="warn" data-testid="rec-fallback">⚠ Fell back to
            {run.result?.affinity_requested ? 'ranking (the model returned no usable affinity)' : 'embedding-cosine ranking (no generative model configured)'}.</span>
        {/if}
      </div>

      {#each papers as paper (paper.work_id)}
        <div class="paper entry-card" data-testid="rec-paper">
          <div class="paper-head">
            <strong>{paper.title || '(untitled)'}</strong>
            <span class="paper-actions">
              <button type="button" class="secondary small" on:click={() => (rawScoresPaper = paper)}>Scores</button>
              <button type="button" class="secondary small" on:click={() => (rawLlmPaper = paper)}>Raw LLM</button>
            </span>
          </div>

          {#if run.mode === 'tags'}
            {#if (paper.suggestions ?? []).length === 0}
              <p class="muted">No tag suggestions (no unassigned tags offered for this paper).</p>
            {:else}
              <ul class="picks">
                {#each paper.suggestions ?? [] as s (s.tag_id)}
                  <li>
                    <label>
                      <input type="checkbox" checked={(chosenTags[paper.work_id] ?? new Set()).has(s.tag_id)}
                        on:change={() => (chosenTags = toggle(chosenTags, paper.work_id, s.tag_id))} />
                      <span class="rank">#{s.rank}</span> {s.name}
                      {#if s.affinity != null}<span class="aff">{Math.round(s.affinity)}</span>{/if}
                    </label>
                  </li>
                {/each}
              </ul>
              <button type="button" class="small" disabled={!((chosenTags[paper.work_id] ?? new Set()).size)}
                on:click={() => acceptTags(paper)}>Apply selected tags</button>
            {/if}
          {:else}
            {#if (paper.shelves ?? []).length === 0}
              <p class="muted">No shelf suggestions for this paper.</p>
            {:else}
              <ul class="picks">
                {#each paper.shelves ?? [] as sh (sh.shelf_id)}
                  <li title={shelfDescription(sh.shelf_id) || 'No description'}>
                    <label>
                      <input type="checkbox" checked={(chosenShelves[paper.work_id] ?? new Set()).has(sh.shelf_id)}
                        on:change={() => (chosenShelves = toggle(chosenShelves, paper.work_id, sh.shelf_id))} />
                      <span class="rank">score {sh.score}</span> {sh.name}
                      {#if sh.affinity != null}<span class="aff">{Math.round(sh.affinity)}</span>{/if}
                    </label>
                  </li>
                {/each}
              </ul>
              <button type="button" class="small" disabled={!((chosenShelves[paper.work_id] ?? new Set()).size)}
                on:click={() => acceptShelves(paper)}>Add to selected shelves</button>
            {/if}
          {/if}
          {#if accepted[paper.work_id]}<span class="ok">{accepted[paper.work_id]}</span>{/if}
        </div>
      {/each}
    {/if}
  {/if}
</div>

{#if rawScoresPaper}
  <Modal title={`Scores — ${rawScoresPaper.title || 'paper'}`} onClose={() => (rawScoresPaper = null)}>
    {#if rawScoresPaper.per_kind}
      {#each ['row', 'rack', 'shelf'] as kind (kind)}
        <h4>{kind}s</h4>
        <table class="scores"><tbody>
          {#each rawScoresPaper.per_kind[kind] as p (p.id)}
            <tr><td>#{p.rank}</td><td>{p.name}</td><td>base {p.base}</td><td>{p.affinity != null ? `aff ${Math.round(p.affinity)}` : '—'}</td></tr>
          {/each}
        </tbody></table>
      {/each}
      <h4>Combined shelves</h4>
      <table class="scores"><tbody>
        {#each rawScoresPaper.shelves ?? [] as sh (sh.shelf_id)}
          <tr><td>{sh.name}</td><td>score {sh.score}</td><td>base {sh.base}</td><td>+boost {sh.parent_boost}</td></tr>
        {/each}
      </tbody></table>
    {:else}
      <table class="scores"><tbody>
        {#each rawScoresPaper.suggestions ?? [] as s (s.tag_id)}
          <tr><td>#{s.rank}</td><td>{s.name}</td><td>base {s.base}</td><td>{s.affinity != null ? `aff ${Math.round(s.affinity)}` : '—'}</td></tr>
        {/each}
      </tbody></table>
    {/if}
  </Modal>
{/if}

{#if rawLlmPaper}
  <Modal title={`Raw LLM I/O — ${rawLlmPaper.title || 'paper'}`} onClose={() => (rawLlmPaper = null)}>
    {#each Object.entries(rawLlmPaper.raw ?? {}) as [kind, io] (kind)}
      <h4>{kind}</h4>
      <p class="muted">Input</p><pre class="raw">{io.input}</pre>
      <p class="muted">Output</p><pre class="raw">{io.output}</pre>
    {/each}
    {#if !Object.keys(rawLlmPaper.raw ?? {}).length}<p class="muted">No raw LLM I/O recorded.</p>{/if}
  </Modal>
{/if}

<style>
  .controls { display: flex; flex-direction: column; gap: 0.6rem; }
  .options { align-items: end; display: flex; flex-wrap: wrap; gap: 0.6rem; }
  .options label { display: grid; font-size: 0.8rem; gap: 0.15rem; }
  .options .toggle { align-items: center; display: flex; flex-direction: row; gap: 0.3rem; }
  .num { width: 4rem; }
  .msg { color: var(--status-danger); }
  .status { color: var(--ink-muted); margin: 0.6rem 0; }
  .run-meta { display: flex; flex-wrap: wrap; gap: 0.8rem; margin: 0.5rem 0; }
  .warn { color: var(--status-warning); font-weight: 600; }
  .paper { display: flex; flex-direction: column; gap: 0.35rem; margin: 0.5rem 0; padding: 0.6rem; }
  .paper-head { align-items: center; display: flex; justify-content: space-between; }
  .paper-actions { display: flex; gap: 0.3rem; }
  .picks { display: flex; flex-direction: column; gap: 0.2rem; list-style: none; margin: 0.2rem 0; padding: 0; }
  .rank { color: var(--ink-muted); font-size: 0.78rem; }
  .aff { background: var(--status-success-bg); border-radius: 999px; color: var(--status-success); font-size: 0.72rem; padding: 0.02rem 0.4rem; }
  .ok { color: var(--status-success); font-size: 0.8rem; }
  .small { min-height: 1.9rem; padding: 0.2rem 0.55rem; }
  .scores { border-collapse: collapse; font-size: 0.82rem; width: 100%; }
  .scores td { border-bottom: 1px solid var(--border-normal); padding: 0.15rem 0.4rem; }
  .raw { background: var(--surface-sunken); border-radius: 6px; max-height: 12rem; overflow: auto; padding: 0.5rem; white-space: pre-wrap; word-break: break-word; }
</style>
