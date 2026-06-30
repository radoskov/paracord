<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  import { ApiClient, type QueueStatus } from '../api/client';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let status: QueueStatus | null = null;
  let message = '';
  let auto = true;
  let timer: ReturnType<typeof setInterval> | null = null;
  let filter = 'all';

  const COUNT_ORDER = ['queued', 'started', 'finished', 'failed', 'scheduled', 'deferred'];

  $: visibleJobs = (status?.jobs ?? []).filter((j) => filter === 'all' || j.status === filter);

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
    try {
      status = await client.getJobs(40);
      message = '';
    } catch (error) {
      message = errorMessage(error);
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
        <label class="auto"><input type="checkbox" checked={auto} on:change={toggleAuto} /> Auto-refresh</label>
        <button type="button" class="secondary" on:click={refresh} title="Refresh now">Refresh</button>
        <button type="button" class="secondary" on:click={clean}
          title="Clear finished and failed job history (running jobs are kept)">Clean</button>
      </div>
    </div>
    <p class="muted">
      PDF extraction (GROBID) and metadata enrichment run here in the background worker. If a task
      seems stuck, check that the worker is available below.
    </p>

    {#if message}<p class="danger">{message}</p>{/if}

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
          <button type="button" class="count count-all" class:active={filter === 'all'} on:click={() => (filter = 'all')}>
            <span class="n">{status.jobs.length}</span>
            <span class="k">all</span>
          </button>
          {#each COUNT_ORDER as key (key)}
            <button type="button" class="count count-{key}" class:active={filter === key} on:click={() => setFilter(key)}
              title={`Show only ${key} jobs`}>
              <span class="n">{status.counts[key] ?? 0}</span>
              <span class="k">{key}</span>
            </button>
          {/each}
        </div>

        {#if status.jobs.length === 0}
          <p class="empty">No recent jobs. Import a PDF or click Enrich on a paper to create one.</p>
        {:else if visibleJobs.length === 0}
          <p class="empty">No <strong>{filter}</strong> jobs in the recent window. <button type="button" class="linkish" on:click={() => (filter = 'all')}>Show all</button></p>
        {:else}
          <ul class="jobs">
            {#each visibleJobs as job (job.id)}
              <li>
                <div class="job-row">
                  <span class="badge badge-{job.status}">{job.status}</span>
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

  .auto {
    align-items: center;
    color: #44515f;
    display: flex;
    flex-direction: row;
    font-weight: 600;
    gap: 0.3rem;
  }

  .warn {
    background: #fff7ed;
    border-color: #fdba74;
    color: #7c2d12;
    text-align: left;
  }

  .counts {
    display: grid;
    gap: 0.6rem;
    grid-template-columns: repeat(auto-fit, minmax(6rem, 1fr));
    margin: 0.8rem 0;
  }

  .count {
    background: #f4f6f9;
    border: 1px solid #e1e7ee;
    border-radius: 8px;
    color: inherit;
    cursor: pointer;
    display: block;
    padding: 0.6rem;
    text-align: center;
    width: 100%;
  }

  .count:hover {
    border-color: #b9c4d0;
  }

  .count.active {
    border-color: #2d3e50;
    box-shadow: inset 0 0 0 1px #2d3e50;
  }

  .linkish {
    background: none;
    border: none;
    color: #2563eb;
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
    color: #64717f;
    font-size: 0.78rem;
    text-transform: uppercase;
  }

  .count-failed .n {
    color: #b3261e;
  }

  .count-started .n {
    color: #1d4ed8;
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
    border-bottom: 1px solid #eef1f4;
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
    color: #5a6675;
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
    background: #eef1f5;
    border-radius: 0.25rem;
    color: #44515f;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.72rem;
    padding: 0.05rem 0.35rem;
  }

  .err-details summary {
    color: #b3261e;
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
    background: #e2e8f0;
    color: #334155;
  }

  .badge-started {
    background: #bfdbfe;
    color: #1e3a5f;
  }

  .badge-finished {
    background: #bbf7d0;
    color: #14532d;
  }

  .badge-failed {
    background: #fecaca;
    color: #7f1d1d;
  }

  .err {
    background: #fff5f5;
    border: 1px solid #f1d0cc;
    border-radius: 4px;
    color: #7f1d1d;
    font-size: 0.74rem;
    margin: 0.3rem 0 0;
    max-height: 16rem;
    overflow: auto;
    padding: 0.5rem;
    white-space: pre-wrap;
    word-break: break-word;
  }
</style>
