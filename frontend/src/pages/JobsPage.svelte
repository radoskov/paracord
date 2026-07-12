<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  import { ApiClient, type QueueStatus } from '../api/client';
  import { canManageUsers } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  // Queue/worker recovery controls are owner/admin-only (they empty or reset shared queue state);
  // `canManageUsers` is the owner-or-admin gate that mirrors the endpoints' require_admin.
  // Whether this tab is the active one. Tabs stay mounted across switches (#9); pause polling
  // while hidden so background tabs don't keep hitting the API.
  export let visible = true;

  let status: QueueStatus | null = null;
  let message = '';
  let auto = true;
  let timer: ReturnType<typeof setInterval> | null = null;
  let filter = 'all';
  // `loaded` flips true once the first refresh settles (success OR failure) so "Loading…" only
  // shows before the first response, never permanently. `loading` guards against overlapping polls
  // stacking up (the 4s timer must not fire a second request while one is still in flight).
  let loaded = false;
  let loading = false;

  // Defensive normalisation: a payload missing `counts`/`jobs` (older server, or a partial/error
  // response with available:true) must never let the template throw during render — a thrown error
  // mid-render freezes the mounted tab so it looks unclickable. Coerce to always-safe shapes.
  function normalize(s: QueueStatus): QueueStatus {
    return {
      ...s,
      counts: s.counts ?? {},
      jobs: Array.isArray(s.jobs) ? s.jobs : [],
    };
  }

  // Pause the auto-refresh timer when the tab is hidden; resume (and refresh once) when shown.
  let wasVisible = true;
  $: {
    if (visible && !wasVisible) {
      void refresh();
      startAuto();
    } else if (!visible && wasVisible) {
      stopAuto();
    }
    wasVisible = visible;
  }

  const COUNT_ORDER = ['queued', 'started', 'finished', 'failed', 'scheduled', 'deferred'];

  $: visibleJobs = (status?.jobs ?? []).filter((j) => filter === 'all' || j.status === filter);

  // Queue-health semaphore (D7): GREEN reachable + workers draining, YELLOW reachable but no
  // worker consuming (jobs pile up), RED Redis unreachable (imports won't be processed).
  $: reachable = status ? (status.redis_reachable ?? status.available) : false;
  $: workerCount = status ? (status.worker_count ?? status.workers ?? 0) : 0;
  $: queued = status ? (status.queued ?? status.counts?.queued ?? 0) : 0;
  $: health = !status
    ? null
    : !reachable
      ? {
          color: 'red',
          // E1: when the server requires Redis (fail-closed), an outage also means rate/queue
          // limits are unavailable and Redis-dependent requests are being rejected with 503.
          text: status.require_redis
            ? 'Rate & queue limits unavailable (Redis unreachable) — this server requires Redis, so imports and other requests are being rejected (503) until it is back'
            : "Processing queue offline (Redis unreachable) — imports won't be processed until it's back",
        }
      : workerCount === 0
        ? { color: 'yellow', text: `Queue reachable but no workers running · ${queued} queued` }
        : {
            color: 'green',
            text: `Queue healthy · ${workerCount} worker${workerCount === 1 ? '' : 's'} · ${queued} queued`,
          };

  function setFilter(key: string): void {
    filter = filter === key ? 'all' : key;
  }

  async function clean(): Promise<void> {
    if (!window.confirm('Clear finished and failed job history? Running jobs are kept.')) return;
    try {
      const result = await client.clearJobs('finished_failed');
      message = result.available ? `Cleared ${result.cleared} job(s)` : `Unavailable: ${result.error ?? ''}`;
      await refresh();
    } catch (error) {
      message = errorMessage(error);
    }
  }

  async function clearQueue(): Promise<void> {
    if (!window.confirm('Empty the pending job queue? Running jobs are kept, but every waiting task is dropped.')) return;
    try {
      const result = await client.clearQueue();
      await refresh(); // refresh() clears `message` on success, so set the result note afterwards
      message = result.available ? `Dropped ${result.dropped} pending job(s)` : `Unavailable: ${result.error ?? ''}`;
    } catch (error) {
      message = errorMessage(error);
    }
  }

  async function resetWorkers(): Promise<void> {
    if (!window.confirm('Reset workers? Jobs stuck as "started" are requeued and failed history is cleared. To fully restart the worker processes, run `docker compose restart worker`.')) return;
    try {
      const result = await client.resetWorkers();
      await refresh(); // refresh() clears `message` on success, so set the result note afterwards
      message = result.available
        ? `Requeued ${result.requeued} stuck job(s), cleared ${result.cleared_failed} failed. ${result.note}`
        : `Unavailable: ${result.error ?? ''}`;
    } catch (error) {
      message = errorMessage(error);
    }
  }

  onMount(() => {
    void refresh();
    startAuto();
  });

  onDestroy(stopAuto);

  function startAuto(): void {
    stopAuto();
    if (auto) timer = setInterval(() => void refresh(), 4000);
  }

  function stopAuto(): void {
    if (timer) clearInterval(timer);
    timer = null;
  }

  function toggleAuto(): void {
    auto = !auto;
    startAuto();
  }

  async function refresh(): Promise<void> {
    if (loading) return; // don't stack overlapping polls behind an in-flight request
    loading = true;
    try {
      status = normalize(await client.getJobs(40));
      message = '';
    } catch (error) {
      // Keep the last-good `status` so the tab stays interactive and shows the error line, rather
      // than blanking back to "Loading…". Only a failed *first* load (status still null) falls
      // through to the load-failed placeholder below.
      message = errorMessage(error);
    } finally {
      loaded = true;
      loading = false;
    }
  }

  function fmt(iso: string | null): string {
    return iso ? new Date(iso).toLocaleTimeString() : '—';
  }
</script>

<section class="layout">
  <div class="card">
    <div class="head">
      <h2>Background jobs</h2>
      <div class="controls">
        <label class="auto"><input type="checkbox" checked={auto} on:change={toggleAuto} title="Reload the job list every few seconds" /> Auto-refresh</label>
        <button type="button" class="secondary" on:click={refresh} title="Refresh now">Refresh</button>
        <button type="button" class="secondary" on:click={clean}
          title="Clear finished and failed job history (running jobs are kept)">Clean</button>
        {#if $canManageUsers}
          <button type="button" class="secondary danger-btn" on:click={clearQueue}
            title="Empty the pending job queue (running jobs are kept)">Clear queue</button>
          <button type="button" class="secondary danger-btn" on:click={resetWorkers}
            title="Requeue stuck jobs and clear failed history (a full worker restart is docker compose restart worker)">Reset workers</button>
        {/if}
      </div>
    </div>
    <p class="muted">
      PDF extraction (GROBID) and metadata enrichment run here in the background worker. If a task
      seems stuck, check that the worker is available below.
    </p>

    {#if message}<p class="danger">{message}</p>{/if}

    {#if health}
      <div class="semaphore semaphore-{health.color}" role="status" data-testid="queue-health">
        <span class="light light-{health.color}" aria-hidden="true"></span>
        <span class="semaphore-text">{health.text}</span>
      </div>
    {/if}

    {#if status}
      {#if !status.available}
        <p class="empty warn">
          ⚠ Background worker / Redis is <strong>unavailable</strong> — queued tasks won’t run.
          Start the stack with <code>make up</code> (it launches the <code>worker</code> service),
          then refresh. {status.error ? `(${status.error})` : ''}
        </p>
      {:else}
        <p class="muted">
          {status.workers} worker{status.workers === 1 ? '' : 's'} connected.
          {#if status.workers === 0}
            <strong class="danger">No worker is consuming the queue — start the <code>worker</code> service.</strong>
          {/if}
        </p>
        <div class="counts">
          <button type="button" class="count count-all" class:active={filter === 'all'} on:click={() => (filter = 'all')}
            title="Show jobs of every status">
            <span class="n">{status.jobs?.length ?? 0}</span>
            <span class="k">all</span>
          </button>
          {#each COUNT_ORDER as key (key)}
            <button type="button" class="count count-{key}" class:active={filter === key} on:click={() => setFilter(key)}
              title={`Show only ${key} jobs`}>
              <span class="n">{status.counts?.[key] ?? 0}</span>
              <span class="k">{key}</span>
            </button>
          {/each}
        </div>

        {#if (status.jobs?.length ?? 0) === 0}
          <p class="empty">No recent jobs. Import a PDF or click Enrich on a paper to create one.</p>
        {:else if visibleJobs.length === 0}
          <p class="empty">
            {#if (status.counts?.[filter] ?? 0) > 0}
              {status.counts[filter]} <strong>{filter}</strong> job{status.counts[filter] === 1 ? '' : 's'} in the queue registry, but none are in the recent window — they may be older than the list or already cleared. Use <em>Clean</em> to clear finished/failed history.
            {:else}
              No <strong>{filter}</strong> jobs in the recent window.
            {/if}
            <button type="button" class="linkish" on:click={() => (filter = 'all')} title="Show jobs of every status">Show all</button>
          </p>
        {:else}
          <ul class="jobs">
            {#each visibleJobs as job (job.id)}
              <li>
                <div class="job-row">
                  <span class="badge badge-{job.status}">{job.status}</span>
                  {#if job.retries_left != null}
                    <small class="muted" title="Automatic retries remaining before this is marked failed">↻ {job.retries_left} left</small>
                  {/if}
                  <strong>{job.task}</strong>
                  <small class="muted">{fmt(job.enqueued_at)}{job.ended_at ? ` → ${fmt(job.ended_at)}` : ''}</small>
                </div>
                {#if job.paper_title || job.paper_sha256}
                  <div class="paper">
                    {#if job.paper_title}<span class="paper-title">{job.paper_title}</span>{/if}
                    {#if job.paper_sha256}<span class="hash" title={job.paper_sha256}>{job.paper_sha256.slice(0, 12)}…</span>{/if}
                  </div>
                {/if}
                {#if job.error}
                  <details class="err-details">
                    <summary>error — {job.error.split('\n').filter(Boolean).slice(-1)[0]?.slice(0, 140)}</summary>
                    <pre class="err">{job.error}</pre>
                  </details>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      {/if}
    {:else if loaded}
      <p class="empty warn">
        Couldn’t load the background jobs list.
        <button type="button" class="linkish" on:click={refresh} title="Try loading the job list again">Retry</button>
      </p>
    {:else}
      <p class="empty">Loading…</p>
    {/if}
  </div>
</section>

<style>
  .head {
    align-items: center;
    display: flex;
    justify-content: space-between;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0;
  }

  .controls {
    align-items: center;
    display: flex;
    gap: 0.75rem;
  }

  .danger-btn {
    border-color: var(--status-danger-border);
    color: var(--status-danger);
  }

  .auto {
    align-items: center;
    color: var(--ink-normal);
    display: flex;
    flex-direction: row;
    font-weight: 600;
    gap: 0.3rem;
  }

  .warn {
    background: var(--status-warning-bg);
    border-color: var(--status-warning-border);
    color: var(--status-warning);
    text-align: left;
  }

  .semaphore {
    align-items: center;
    border: 1px solid transparent;
    border-radius: 8px;
    display: flex;
    font-weight: 600;
    gap: 0.5rem;
    margin: 0.6rem 0;
    padding: 0.5rem 0.75rem;
  }

  .semaphore-green {
    background: var(--status-success-bg);
    border-color: var(--status-success-border);
    color: var(--status-success);
  }

  .semaphore-yellow {
    background: var(--status-warning-bg);
    border-color: var(--status-warning-border);
    color: var(--status-warning);
  }

  .semaphore-red {
    background: var(--status-danger-bg);
    border-color: var(--status-danger-border);
    color: var(--status-danger);
  }

  .light {
    border-radius: 50%;
    display: inline-block;
    flex: none;
    height: 0.75rem;
    width: 0.75rem;
  }

  .light-green {
    background: var(--status-success);
  }

  .light-yellow {
    background: var(--status-warning);
  }

  .light-red {
    background: var(--status-danger);
  }

  .counts {
    display: grid;
    gap: 0.6rem;
    grid-template-columns: repeat(auto-fit, minmax(6rem, 1fr));
    margin: 0.8rem 0;
  }

  .count {
    background: var(--surface-sunken);
    border: 1px solid var(--border-normal);
    border-radius: 8px;
    color: inherit;
    cursor: pointer;
    display: block;
    padding: 0.6rem;
    text-align: center;
    width: 100%;
  }

  .count:hover {
    border-color: var(--border-strong);
  }

  .count.active {
    border-color: var(--accent-primary);
    box-shadow: inset 0 0 0 1px var(--accent-primary);
  }

  .linkish {
    background: none;
    border: none;
    color: var(--accent-link);
    cursor: pointer;
    min-height: auto;
    padding: 0;
    text-decoration: underline;
  }

  .count .n {
    display: block;
    font-size: 1.5rem;
    font-weight: 700;
  }

  .count .k {
    color: var(--ink-muted);
    font-size: 0.78rem;
    text-transform: uppercase;
  }

  .count-failed .n {
    color: var(--status-danger);
  }

  .count-started .n {
    color: var(--status-info);
  }

  .jobs {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .jobs li {
    border-bottom: 1px solid var(--border-normal);
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    padding: 0.4rem 0;
  }

  .job-row {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .paper {
    align-items: baseline;
    color: var(--ink-muted);
    display: flex;
    flex-wrap: wrap;
    font-size: 0.78rem;
    gap: 0.4rem;
    margin-top: 0.2rem;
  }

  .paper-title {
    overflow-wrap: anywhere;
  }

  .hash {
    background: var(--surface-sunken);
    border-radius: 0.25rem;
    color: var(--ink-normal);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.72rem;
    padding: 0.05rem 0.35rem;
  }

  .err-details summary {
    color: var(--status-danger);
    cursor: pointer;
    font-size: 0.78rem;
  }

  .badge {
    border-radius: 0.25rem;
    font-size: 0.72rem;
    font-weight: 700;
    padding: 0.1rem 0.4rem;
    text-transform: uppercase;
  }

  .badge-queued {
    background: var(--surface-sunken);
    color: var(--ink-normal);
  }

  .badge-started {
    background: var(--status-info-bg);
    color: var(--status-info);
  }

  .badge-finished {
    background: var(--status-success-bg);
    color: var(--status-success);
  }

  .badge-failed {
    background: var(--status-danger-bg);
    color: var(--status-danger);
  }

  .err {
    background: var(--status-danger-bg);
    border: 1px solid var(--status-danger-border);
    border-radius: 4px;
    color: var(--status-danger);
    font-size: 0.74rem;
    margin: 0.3rem 0 0;
    max-height: 16rem;
    overflow: auto;
    padding: 0.5rem;
    white-space: pre-wrap;
    word-break: break-word;
  }
</style>
