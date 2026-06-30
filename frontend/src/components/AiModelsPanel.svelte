<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type AiConfig,
    type AiModel,
    type AiProviders,
  } from '../api/client';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let config: AiConfig | null = null;
  let allowed: Record<string, string[]> = {};
  let providers: AiProviders | null = null;
  let models: AiModel[] = [];
  let reindex: { model_name: string; indexed: number; total: number } | null = null;

  let pullProvider = 'ollama';
  let pullModel = '';
  let message = '';
  let busy = false;

  onMount(refresh);

  async function run(fn: () => Promise<void>, ok?: string): Promise<void> {
    busy = true;
    message = '';
    try {
      await fn();
      if (ok) message = ok;
    } catch (error) {
      message = errorMessage(error);
    } finally {
      busy = false;
    }
  }

  async function refresh(): Promise<void> {
    await run(async () => {
      const [cfg, prov, mdl, status] = await Promise.all([
        client.getAiConfig(),
        client.getAiProviders(),
        client.listAiModels(),
        client.getReindexStatus(),
      ]);
      config = cfg.config;
      allowed = cfg.allowed;
      providers = prov;
      models = mdl.models;
      reindex = status;
    });
  }

  function avail(group: 'embedding' | 'summary' | 'topic', key: string): boolean {
    return providers?.[group]?.[key]?.available ?? true;
  }
  function note(group: 'embedding' | 'summary' | 'topic', key: string): string | null {
    return providers?.[group]?.[key]?.note ?? null;
  }

  async function save(): Promise<void> {
    if (!config) return;
    await run(async () => {
      const result = await client.updateAiConfig(config!);
      config = result.config;
      message = result.reindex_job_id
        ? `Saved. Embedding model changed — reindex queued (job ${result.reindex_job_id.slice(0, 8)}).`
        : 'Saved.';
      await refresh();
    });
  }

  async function pull(): Promise<void> {
    if (!pullModel.trim()) return;
    await run(async () => {
      const r = await client.pullAiModel(pullProvider, pullModel.trim());
      message = `Pull queued for ${pullProvider}/${pullModel} (job ${r.job_id.slice(0, 8)}) — watch the Jobs tab.`;
      pullModel = '';
    });
  }

  async function remove(m: AiModel): Promise<void> {
    if (!window.confirm(`Delete model ${m.name}?`)) return;
    await run(async () => {
      await client.deleteAiModel(m.provider, m.name);
      models = await client.listAiModels().then((r) => r.models);
    }, 'Model deleted');
  }

  async function doReindex(): Promise<void> {
    await run(async () => {
      const r = await client.reindexEmbeddings();
      message = `Reindex queued (job ${r.job_id.slice(0, 8)}) — watch the Jobs tab.`;
    });
  }

  function fmtSize(bytes: number | null): string {
    if (!bytes) return '';
    const gb = bytes / 1e9;
    return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / 1e6).toFixed(0)} MB`;
  }
</script>

<section class="card">
  <h2>AI &amp; Models</h2>
  <p class="muted">
    Choose the engines used for semantic search, summaries, and topics, and download local models.
    The dependency-free baselines (hash-BOW / extractive / TF-IDF) always work; heavier providers
    show a hint when they aren't available yet.
  </p>
  {#if message}<p class="message">{message}</p>{/if}

  {#if config}
    <div class="grid">
      <label>Embedding provider
        <select bind:value={config.embedding_provider} disabled={busy}>
          {#each allowed.embedding_provider ?? [] as p}
            <option value={p} disabled={!avail('embedding', p)}>
              {p}{avail('embedding', p) ? '' : ' (unavailable)'}
            </option>
          {/each}
        </select>
        {#if note('embedding', config.embedding_provider)}
          <small class="hint">{note('embedding', config.embedding_provider)}</small>
        {/if}
      </label>
      <label>Embedding model
        <input bind:value={config.embedding_model} placeholder="(provider default)" disabled={busy} />
      </label>

      <label>Summary provider
        <select bind:value={config.summary_provider} disabled={busy}>
          {#each allowed.summary_provider ?? [] as p}
            <option value={p} disabled={!avail('summary', p)}>
              {p}{avail('summary', p) ? '' : ' (unavailable)'}
            </option>
          {/each}
        </select>
        {#if note('summary', config.summary_provider)}
          <small class="hint">{note('summary', config.summary_provider)}</small>
        {/if}
      </label>
      <label>Summary model (Ollama)
        <input bind:value={config.summary_model} disabled={busy} />
      </label>

      <label>Topic backend
        <select bind:value={config.topic_backend} disabled={busy}>
          {#each allowed.topic_backend ?? [] as p}<option value={p}>{p}</option>{/each}
        </select>
      </label>
      <label>Ollama URL
        <input bind:value={config.ollama_url} placeholder="http://localhost:11434" disabled={busy} />
      </label>
    </div>
    <div class="row">
      <button type="button" on:click={save} disabled={busy}>Save config</button>
      <button type="button" class="secondary" on:click={refresh} disabled={busy}>Refresh</button>
      {#if providers}
        <span class="muted">Ollama: {providers.ollama_reachable ? 'reachable ✓' : 'not reachable'}</span>
      {/if}
    </div>

    <h3>Models</h3>
    <div class="row">
      <select bind:value={pullProvider} disabled={busy}>
        <option value="ollama">ollama</option>
        <option value="sentence_transformers">sentence_transformers</option>
      </select>
      <input bind:value={pullModel} placeholder="model name (e.g. nomic-embed-text)" disabled={busy} />
      <button type="button" class="secondary" on:click={pull} disabled={busy || !pullModel.trim()}>
        Pull model
      </button>
    </div>
    {#if models.length === 0}
      <p class="empty">No local models. Pull one above (needs the Ollama profile running).</p>
    {:else}
      <ul class="models">
        {#each models as m (m.provider + m.name)}
          <li>
            <span>{m.name} <small class="muted">{m.provider} {fmtSize(m.size_bytes)}</small></span>
            <button type="button" class="secondary small" on:click={() => remove(m)} disabled={busy}>Delete</button>
          </li>
        {/each}
      </ul>
    {/if}

    <h3>Embedding index</h3>
    {#if reindex}
      <p class="muted">
        <strong>{reindex.indexed}</strong> / {reindex.total} works indexed for
        <code>{reindex.model_name}</code>.
      </p>
    {/if}
    <button type="button" class="secondary" on:click={doReindex} disabled={busy}>Reindex embeddings</button>
  {:else}
    <p class="empty">Loading…</p>
  {/if}
</section>

<style>
  h2 { font-size: 1.05rem; margin: 0 0 0.4rem; }
  h3 { font-size: 0.9rem; margin: 1rem 0 0.4rem; }
  .grid {
    display: grid;
    gap: 0.6rem;
    grid-template-columns: 1fr 1fr;
    margin: 0.6rem 0;
  }
  .row { align-items: center; display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.5rem 0; }
  .hint { color: #8a6d3b; }
  .message { background: #eef4ef; border-radius: 6px; padding: 0.4rem 0.6rem; }
  .models { display: flex; flex-direction: column; gap: 0.3rem; list-style: none; margin: 0.4rem 0; padding: 0; }
  .models li { align-items: center; display: flex; gap: 0.5rem; justify-content: space-between; }
  .small { min-height: 1.9rem; padding: 0.2rem 0.5rem; }
  @media (max-width: 700px) { .grid { grid-template-columns: 1fr; } }
</style>
