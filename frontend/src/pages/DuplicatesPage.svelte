<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type DuplicateCandidate,
    type DuplicateCandidateAction,
    type DuplicateCandidateStatus,
    type DuplicateSplitSegment,
    type MergePreview,
    type Work,
  } from '../api/client';
  import Modal from '../components/Modal.svelte';
  import WorkDetail from '../components/WorkDetail.svelte';
  import { canEdit, INSUFFICIENT_ROLE } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let candidates: DuplicateCandidate[] = [];
  let statusFilter: DuplicateCandidateStatus | '' = 'open';
  // Two sub-tabs (#2): real duplicate/version pairs vs "multi-work file" candidates. The latter are
  // mostly false positives (a PDF that merely looks like it bundles several papers) and, mixed in,
  // they bury the largely-correct duplicate results — so they get their own tab to triage apart.
  let activeTab: 'duplicates' | 'multiwork' = 'duplicates';
  $: multiworkCandidates = candidates.filter((c) => c.candidate_type === 'multiwork_file');
  $: dupeCandidates = candidates.filter((c) => c.candidate_type !== 'multiwork_file');
  $: shownCandidates = activeTab === 'multiwork' ? multiworkCandidates : dupeCandidates;
  let splitDrafts: Record<string, string> = {};
  // Per-candidate chosen base (the surviving canonical paper, "merge INTO"); the other work is the
  // merge-from source. The double-arrow control swaps them. Defaults to the suggested target.
  let baseIds: Record<string, string> = {};
  let previews: Record<string, MergePreview | null> = {};
  let loading = false;
  let message = '';
  // Paper opened in the in-app paper view (WorkDetail modal) to inspect differences before merging.
  let detailWork: Work | null = null;

  async function openInPaperView(workId: string): Promise<void> {
    try {
      detailWork = await client.getWork(workId);
    } catch (error) {
      message = errorMessage(error);
    }
  }

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
      candidates = await client.listDuplicateCandidates(statusFilter);
      previews = {};
      for (const c of candidates) {
        if (canResolveAsWork(c) && c.status === 'open') void loadPreview(c);
      }
    });
  }

  function baseId(c: DuplicateCandidate): string {
    return baseIds[c.id] ?? c.suggested_target_work_id ?? c.entity_a_id;
  }
  function sourceId(c: DuplicateCandidate): string {
    return baseId(c) === c.entity_a_id ? c.entity_b_id : c.entity_a_id;
  }
  function entLabel(c: DuplicateCandidate, id: string): string {
    if (id === c.entity_a_id) return c.entity_a_label ?? `${c.entity_a_type}:${id.slice(0, 8)}`;
    return c.entity_b_label ?? `${c.entity_b_type}:${id.slice(0, 8)}`;
  }
  function swap(c: DuplicateCandidate): void {
    baseIds = { ...baseIds, [c.id]: sourceId(c) };
    void loadPreview(c);
  }
  async function loadPreview(c: DuplicateCandidate): Promise<void> {
    previews = { ...previews, [c.id]: undefined as unknown as MergePreview };
    try {
      previews = { ...previews, [c.id]: await client.getMergePreview(c.id, baseId(c)) };
    } catch {
      previews = { ...previews, [c.id]: null };
    }
  }
  function mergeSummary(p: MergePreview | null | undefined): string {
    if (p === undefined) return 'Loading preview…';
    // null = the preview request errored or timed out. Say so explicitly (and point at the paper
    // links) rather than silently hiding the line, which read as a permanent "Loading preview…".
    if (p === null) return 'Preview unavailable — open the papers below to compare them directly.';
    const text =
      `Merge: fills ${p.fill_fields.length} empty field(s), adds ${p.conflict_fields.length} ` +
      `conflict(s), moves ${p.file_count} file(s), hides the other as a shadow.`;
    return p.will_flatten ? `${text} (finalizes a prior merge first)` : text;
  }

  // Wait for the queued full-library scan job to leave the queue before reloading candidates: a
  // full scan runs on the worker (D15), so the candidates it produces are not visible in the
  // response. Polls the jobs list for the job's terminal state; bounded so a stuck/unavailable
  // queue still returns control. Returns the failure message, or '' on success.
  async function waitForScanJob(jobId: string): Promise<string> {
    let seen = false;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      let job;
      try {
        const status = await client.getJobs(50);
        job = status.jobs.find((j) => j.id === jobId);
      } catch {
        job = undefined; // queue introspection unavailable — fall through to the reload below
      }
      if (job) {
        seen = true;
        if (job.status === 'finished') return '';
        if (job.status === 'failed') return 'Duplicate scan failed on the worker.';
      } else if (seen) {
        return ''; // the job completed and rolled out of the recent-jobs window
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    return '';
  }

  async function scan(): Promise<void> {
    await run(async () => {
      const result = await client.scanDuplicateCandidates();
      let failure = '';
      if (result.queued && result.job_id) {
        message = 'Scan running…';
        failure = await waitForScanJob(result.job_id);
      }
      candidates = await client.listDuplicateCandidates(statusFilter);
      message = failure || `Scan complete: ${candidates.length} candidate(s) found`;
    });
  }

  async function apply(candidate: DuplicateCandidate, action: DuplicateCandidateAction): Promise<void> {
    await run(async () => {
      await client.applyDuplicateCandidateAction(candidate.id, action, {
        // Merge/Link honour the user-chosen base (item #1); the swap control picks which survives.
        targetWorkId: canResolveAsWork(candidate) ? baseId(candidate) : undefined,
      });
      candidates = await client.listDuplicateCandidates(statusFilter);
    }, `Applied ${label(action)}`);
  }

  async function split(candidate: DuplicateCandidate): Promise<void> {
    await run(async () => {
      const segments = parseSplit(splitDrafts[candidate.id] ?? '');
      await client.applyDuplicateCandidateAction(candidate.id, 'split_file', { splitSegments: segments });
      splitDrafts = { ...splitDrafts, [candidate.id]: '' };
      candidates = await client.listDuplicateCandidates(statusFilter);
    }, 'File split recorded');
  }

  async function reopen(candidate: DuplicateCandidate): Promise<void> {
    await run(async () => {
      await client.updateDuplicateCandidate(candidate.id, 'open');
      candidates = await client.listDuplicateCandidates(statusFilter);
    });
  }

  function label(value: string): string {
    return value.replaceAll('_', ' ');
  }

  function entities(c: DuplicateCandidate): string {
    const a = c.entity_a_label ?? `${c.entity_a_type}:${c.entity_a_id.slice(0, 8)}`;
    const b = c.entity_b_label ?? `${c.entity_b_type}:${c.entity_b_id.slice(0, 8)}`;
    return a === b ? a : `${a} ↔ ${b}`;
  }

  function signals(c: DuplicateCandidate): string {
    const entries = Object.entries(c.signals ?? {});
    if (!entries.length) return 'No signals';
    return entries.slice(0, 4).map(([k, v]) => `${label(k)}: ${String(v)}`).join(' · ');
  }

  function canResolveAsWork(c: DuplicateCandidate): boolean {
    return c.entity_a_type === 'work' && c.entity_b_type === 'work';
  }
  function canResolveAsFile(c: DuplicateCandidate): boolean {
    return c.candidate_type !== 'multiwork_file' && c.entity_a_type === 'file' && c.entity_b_type === 'file';
  }
  function canSplit(c: DuplicateCandidate): boolean {
    return c.candidate_type === 'multiwork_file';
  }

  function parseSplit(value: string): DuplicateSplitSegment[] {
    const segments = value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [title, start, end] = line.split('|').map((p) => p.trim());
        return { title, page_start: start ? Number(start) : undefined, page_end: end ? Number(end) : undefined };
      })
      .filter((s) => s.title);
    if (!segments.length) throw new Error('Enter at least one segment as: Title | start | end');
    return segments;
  }
</script>

<section class="layout">
  <div class="card">
    <div class="head">
      <h2>Duplicate &amp; version review</h2>
      <div class="controls">
        <select bind:value={statusFilter} on:change={load} aria-label="Filter by status" title="Filter candidates by their review status">
          <option value="open">Open</option>
          <option value="accepted">Accepted</option>
          <option value="rejected">Kept separate</option>
          <option value="">All</option>
        </select>
        <button type="button" on:click={scan} disabled={loading || !$canEdit}
          title={$canEdit ? 'Re-scan the whole library for duplicates' : INSUFFICIENT_ROLE}>
          Scan now
        </button>
      </div>
    </div>
    {#if message}<p class="muted">{message}</p>{/if}

    <div class="subtabs" role="tablist" aria-label="Candidate kind">
      <button
        type="button"
        role="tab"
        aria-selected={activeTab === 'duplicates'}
        class:active={activeTab === 'duplicates'}
        on:click={() => (activeTab = 'duplicates')}
        data-testid="dup-tab-duplicates"
        title="Same-DOI/arXiv, fuzzy-title and identical-file pairs — likely the same paper"
      >
        Duplicates <span class="count">{dupeCandidates.length}</span>
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={activeTab === 'multiwork'}
        class:active={activeTab === 'multiwork'}
        on:click={() => (activeTab = 'multiwork')}
        data-testid="dup-tab-multiwork"
        title="Files that look like they bundle several papers — mostly false positives, triaged separately"
      >
        Multi-work files <span class="count">{multiworkCandidates.length}</span>
      </button>
    </div>

    {#if activeTab === 'duplicates'}
      <p class="muted">
        Likely-same papers grouped by signal (same DOI/arXiv, fuzzy title, or identical file). Choose
        how to resolve each one — nothing is deleted.
      </p>
    {:else}
      <p class="muted">
        Files that look like they hold several papers. These are mostly false positives, so review
        them here on their own — split a file into separate papers, or keep it as-is. Nothing is
        deleted.
      </p>
    {/if}

    {#if shownCandidates.length === 0}
      <p class="empty">
        {#if activeTab === 'multiwork'}No multi-work file candidates for this filter.{:else}No
          duplicate candidates for this filter.{/if} Try “Scan now”, or switch the status filter.
      </p>
    {:else}
      <div class="cands">
        {#each shownCandidates as c (c.id)}
          <article>
            <header>
              <div>
                <strong>{label(c.candidate_type)}</strong>
                {#if !canResolveAsWork(c)}<span class="muted">{entities(c)}</span>{/if}
              </div>
              <b>{Math.round(c.score * 100)}%</b>
            </header>
            <p class="muted">{signals(c)}</p>
            {#if canResolveAsWork(c)}
              <div class="pair">
                <div class="side">
                  <span class="tag">Base — merge into</span>
                  <button type="button" class="paper paper-link"
                    on:click={() => openInPaperView(baseId(c))}
                    title={`Open “${entLabel(c, baseId(c))}” in the paper view`}>{entLabel(c, baseId(c))}</button>
                </div>
                <button type="button" class="swap" on:click={() => swap(c)}
                  disabled={loading || !$canEdit || c.status !== 'open'}
                  aria-label="Swap which paper survives as the base"
                  title="Swap: make the other paper the surviving base">⇄</button>
                <div class="side">
                  <span class="tag">Merge from</span>
                  <button type="button" class="paper paper-link"
                    on:click={() => openInPaperView(sourceId(c))}
                    title={`Open “${entLabel(c, sourceId(c))}” in the paper view`}>{entLabel(c, sourceId(c))}</button>
                </div>
              </div>
              {#if c.status === 'open' && mergeSummary(previews[c.id])}
                <p class="preview">{mergeSummary(previews[c.id])}</p>
              {/if}
            {/if}
            <div class="actions">
              {#if canResolveAsWork(c)}
                <button type="button" on:click={() => apply(c, 'merge_works')} disabled={loading || !$canEdit || c.status !== 'open'}
                  title={$canEdit ? 'Consolidate the merge-from paper into the base; hide it as a shadow' : INSUFFICIENT_ROLE}>Merge</button>
                <button type="button" class="secondary" on:click={() => apply(c, 'link_as_version')} disabled={loading || !$canEdit || c.status !== 'open'}
                  title={$canEdit ? 'Keep both papers and their files; record a related / same-work link' : INSUFFICIENT_ROLE}>Link</button>
              {/if}
              {#if canResolveAsFile(c)}
                <button type="button" on:click={() => apply(c, 'mark_duplicate_file')} disabled={loading || !$canEdit || c.status !== 'open'}
                  title={$canEdit ? 'Mark one file as a duplicate copy' : INSUFFICIENT_ROLE}>Mark duplicate</button>
              {/if}
              <button type="button" class="secondary" on:click={() => apply(c, 'keep_separate')} disabled={loading || !$canEdit || c.status !== 'open'}
                title={$canEdit ? 'These are genuinely different — keep them separate (permanent, reviewable under “Kept separate”; reopen anytime)' : INSUFFICIENT_ROLE}>Keep separate</button>
              <button type="button" class="secondary" on:click={() => apply(c, 'ignore')} disabled={loading || !$canEdit || c.status !== 'open'}
                title={$canEdit ? 'Dismiss for now — no permanent flag; it reappears the next time you scan' : INSUFFICIENT_ROLE}>Ignore</button>
              {#if c.status !== 'open'}
                <button type="button" class="secondary" on:click={() => reopen(c)} disabled={loading || !$canEdit}
                  title={$canEdit ? 'Reopen this resolved candidate' : INSUFFICIENT_ROLE}>Reopen</button>
              {/if}
            </div>
            {#if canSplit(c)}
              <div class="split">
                <label for={`split-${c.id}`}>Split this file into separate papers</label>
                <textarea
                  id={`split-${c.id}`}
                  value={splitDrafts[c.id] ?? ''}
                  on:input={(e) => (splitDrafts = { ...splitDrafts, [c.id]: e.currentTarget.value })}
                  placeholder="One per line:  Title | start page | end page"
                  disabled={loading || !$canEdit || c.status !== 'open'}
                ></textarea>
                <button type="button" on:click={() => split(c)} disabled={loading || !$canEdit || c.status !== 'open'}
                  title={$canEdit ? 'Create separate papers from the page ranges above' : INSUFFICIENT_ROLE}>Split file</button>
              </div>
            {/if}
          </article>
        {/each}
      </div>
    {/if}
  </div>
</section>

{#if detailWork}
  <Modal title={detailWork.canonical_title ?? 'Paper'} wide onClose={() => (detailWork = null)}>
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
  .head {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    justify-content: space-between;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0;
  }

  .controls {
    display: flex;
    gap: 0.5rem;
  }

  .subtabs {
    border-bottom: 1px solid var(--border-normal);
    display: flex;
    gap: 0.25rem;
    margin: 0.6rem 0 0.2rem;
  }

  .subtabs button {
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    color: var(--ink-muted);
    cursor: pointer;
    font: inherit;
    font-weight: 600;
    margin-bottom: -1px;
    padding: 0.4rem 0.7rem;
  }

  .subtabs button.active {
    border-bottom-color: var(--accent-primary);
    color: var(--ink-strong);
  }

  .subtabs .count {
    background: var(--surface-sunken);
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 700;
    margin-left: 0.3rem;
    padding: 0.05rem 0.4rem;
  }

  .cands {
    display: grid;
    gap: 0.7rem;
    margin-top: 0.6rem;
  }

  article {
    background: var(--surface-sunken);
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    padding: 0.75rem;
  }

  article header {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
  }

  article header div {
    display: grid;
    gap: 0.15rem;
    min-width: 0;
  }

  .pair {
    align-items: stretch;
    display: flex;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }

  .side {
    background: var(--surface-normal, var(--surface-sunken));
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    display: grid;
    flex: 1;
    gap: 0.2rem;
    min-width: 0;
    padding: 0.4rem 0.55rem;
  }

  .tag {
    color: var(--text-muted);
    font-size: 0.72rem;
    text-transform: uppercase;
  }

  .paper {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* The paper labels are buttons that open the full paper view for side-by-side comparison. */
  .paper-link {
    background: none;
    border: none;
    color: var(--accent, var(--text-link, inherit));
    cursor: pointer;
    display: block;
    font: inherit;
    padding: 0;
    text-align: left;
    text-decoration: underline;
    width: 100%;
  }

  .paper-link:hover {
    text-decoration: none;
  }

  .swap {
    align-self: center;
    font-size: 1.1rem;
    min-height: 2rem;
    padding: 0.2rem 0.5rem;
  }

  .preview {
    color: var(--text-muted);
    font-size: 0.82rem;
    margin: 0.45rem 0 0;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.4rem;
  }

  .actions button {
    min-height: 2rem;
    padding: 0.3rem 0.55rem;
  }

  .split {
    display: grid;
    gap: 0.4rem;
    margin-top: 0.6rem;
  }

  .split textarea {
    min-height: 4rem;
    resize: vertical;
  }
</style>
