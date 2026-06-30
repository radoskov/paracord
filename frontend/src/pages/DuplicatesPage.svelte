<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type DuplicateCandidate,
    type DuplicateCandidateAction,
    type DuplicateCandidateStatus,
    type DuplicateSplitSegment,
  } from '../api/client';
  import { canEdit, INSUFFICIENT_ROLE } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let candidates: DuplicateCandidate[] = [];
  let statusFilter: DuplicateCandidateStatus | '' = 'open';
  let splitDrafts: Record<string, string> = {};
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
      candidates = await client.listDuplicateCandidates(statusFilter);
    });
  }

  async function scan(): Promise<void> {
    await run(async () => {
      const result = await client.scanDuplicateCandidates();
      candidates = await client.listDuplicateCandidates(statusFilter);
      message = `Scan complete: ${result.candidate_count} candidates across ${result.scanned_works} papers and ${result.scanned_files} files`;
    });
  }

  async function apply(candidate: DuplicateCandidate, action: DuplicateCandidateAction): Promise<void> {
    await run(async () => {
      await client.applyDuplicateCandidateAction(candidate.id, action, {
        targetWorkId: canResolveAsWork(candidate)
          ? (candidate.suggested_target_work_id ?? candidate.entity_a_id)
          : undefined,
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
          <option value="rejected">Rejected</option>
          <option value="ignored">Ignored</option>
          <option value="">All</option>
        </select>
        <button type="button" on:click={scan} disabled={loading || !$canEdit}
          title={$canEdit ? 'Re-scan the whole library for duplicates' : INSUFFICIENT_ROLE}>
          Scan now
        </button>
      </div>
    </div>
    {#if message}<p class="muted">{message}</p>{/if}
    <p class="muted">
      Candidates are grouped by signal (same DOI/arXiv, fuzzy title, identical file, or a file that
      looks like it holds several papers). Choose how to resolve each one — nothing is deleted.
    </p>

    {#if candidates.length === 0}
      <p class="empty">No candidates for this filter. Try “Scan now”, or switch the status filter.</p>
    {:else}
      <div class="cands">
        {#each candidates as c (c.id)}
          <article>
            <header>
              <div>
                <strong>{label(c.candidate_type)}</strong>
                <span class="muted">{entities(c)}</span>
              </div>
              <b>{Math.round(c.score * 100)}%</b>
            </header>
            <p class="muted">{signals(c)}</p>
            <div class="actions">
              {#if canResolveAsWork(c)}
                <button type="button" on:click={() => apply(c, 'merge_works')} disabled={loading || !$canEdit || c.status !== 'open'}
                  title={$canEdit ? 'Merge these two papers into one canonical paper' : INSUFFICIENT_ROLE}>Merge</button>
                <button type="button" class="secondary" on:click={() => apply(c, 'link_as_version')} disabled={loading || !$canEdit || c.status !== 'open'}
                  title={$canEdit ? 'Keep both but link one as a version of the other' : INSUFFICIENT_ROLE}>Link version</button>
              {/if}
              {#if canResolveAsFile(c)}
                <button type="button" on:click={() => apply(c, 'mark_duplicate_file')} disabled={loading || !$canEdit || c.status !== 'open'}
                  title={$canEdit ? 'Mark one file as a duplicate copy' : INSUFFICIENT_ROLE}>Mark duplicate</button>
              {/if}
              <button type="button" class="secondary" on:click={() => apply(c, 'keep_separate')} disabled={loading || !$canEdit || c.status !== 'open'}
                title={$canEdit ? 'These are genuinely different — keep them separate' : INSUFFICIENT_ROLE}>Keep separate</button>
              <button type="button" class="secondary" on:click={() => apply(c, 'ignore')} disabled={loading || !$canEdit || c.status !== 'open'}
                title={$canEdit ? 'Dismiss this candidate without a decision' : INSUFFICIENT_ROLE}>Ignore</button>
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

  .cands {
    display: grid;
    gap: 0.7rem;
    margin-top: 0.6rem;
  }

  article {
    background: #f4f6f9;
    border: 1px solid #e1e7ee;
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
