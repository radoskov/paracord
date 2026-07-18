<!-- AiModelsPanel — AI/Models settings: per-capability provider config, model pull/delete, and
     index status (embeddings, lexical).
     Props: client (ApiClient).
     Events/callbacks: none — self-contained; all state is local and persisted via `client`.
     Non-obvious lifecycle/state: on mount, loads status/config/models then validates the
     configured Ollama embedding model so the warning banner is present without user action;
     model-pull progress is tracked by polling the Jobs list (no streaming endpoint) via
     `pollPull`; per-card availability badges are recomputed in a reactive block keyed off
     status/config because the markup can't use {@const} directly under a plain <article>. -->
<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  import {
    ApiClient,
    type AiConfig,
    type AiModel,
    type AiStatus,
    type CatalogModel,
    type EmbeddingModelInfo,
    type LoadedModel,
  } from '../api/client';
  import { errorMessage } from '../lib/ui';
  import Modal from './Modal.svelte';

  export let client: ApiClient;

  let config: AiConfig | null = null;
  let status: AiStatus | null = null;
  let models: AiModel[] = [];

  // Registered embedding models + cap (#21).
  let embeddingModels: EmbeddingModelInfo[] = [];
  let maxModels = 0;

  let pullProvider = 'ollama';
  let pullModel = '';
  let message = '';
  let busy = false;

  // Ollama embedding-model validation (#2): result of validating config.embedding_model.
  let validation: { present: boolean | null; embeddings: boolean | null; canonical: string; error: string | null } | null = null;
  let validating = false;

  // Pull-progress tracking (#5): the job id returned by a pull + its polled status, byte progress,
  // and (on failure) the daemon's actual error text instead of a bare "✗".
  let pullJobId: string | null = null;
  let pullJobStatus = '';
  let pullPolling = false;
  let pullProgress: { done: number; total: number } | null = null;
  let pullError = '';

  // Model search (#5): query + popularity-ranked catalog results (name, size, estimated VRAM).
  let searchQuery = '';
  let searchResults: CatalogModel[] = [];
  let searching = false;
  let searchDone = false;

  // Mount/unmount (#5): models currently loaded in the Ollama daemon's memory.
  let loaded: LoadedModel[] = [];
  // GPU/CPU offload preference applied to the next mount.
  let mountCompute: 'auto' | 'gpu' | 'cpu' = 'auto';
  // Mount/unmount run as background jobs (a load can be slow) — track the job so the UI never blocks.
  let modelJobId: string | null = null;
  let modelJobVerb = ''; // 'Mounting' | 'Unmounting'
  let modelJobStatus = '';
  let modelJobError = '';
  let modelJobPolling = false;

  // Copy-to-clipboard confirmation for a clicked model name (#19).
  let copiedModel = '';

  // Help dialog: a plain-language reference for every option on this tab (owner request 2026-07-18).
  let showHelp = false;

  // #5: keep the Ollama semaphore + loaded-models list live — poll while the tab is visible, and
  // re-check immediately when the tab/window regains focus (so re-opening it shows the true state).
  let refreshTimer: ReturnType<typeof setInterval> | undefined;

  async function refreshLive(): Promise<void> {
    // Lightweight: refresh reachability + loaded only. Deliberately does NOT touch `config` so an
    // in-progress edit isn't clobbered by a background tick.
    try {
      status = await client.getAiStatus();
    } catch {
      /* leave the last-known status */
    }
    try {
      loaded = (await client.getLoadedModels()).loaded;
    } catch {
      /* tolerate */
    }
  }

  function onVisible(): void {
    if (document.visibilityState === 'visible' && !busy) void refreshLive();
  }

  onMount(() => {
    void refresh();
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible' && !busy && !modelJobPolling) void refreshLive();
    }, 8000);
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
  });

  onDestroy(() => {
    if (refreshTimer) clearInterval(refreshTimer);
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('focus', onVisible);
  });

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
      const [st, mdl] = await Promise.all([client.getAiStatus(), client.listAiModels()]);
      status = st;
      config = st.config;
      models = mdl.models;
      // Registered embedding models + cap (#21); tolerate absence on older backends.
      try {
        const emb = await client.listEmbeddingModels();
        embeddingModels = emb.models;
        maxModels = emb.max_models;
      } catch {
        embeddingModels = [];
        maxModels = 0;
      }
      // Loaded-in-memory models (#5); tolerate absence on older backends.
      try {
        loaded = (await client.getLoadedModels()).loaded;
      } catch {
        loaded = [];
      }
    });
    // Validate the current Ollama embedding model so the warning is present on load.
    await validateEmbedding();
  }

  // #2: validate the configured Ollama embedding model against the daemon. Only meaningful for the
  // ollama provider; other providers clear the validation state.
  async function validateEmbedding(): Promise<void> {
    if (!config || config.embedding_provider !== 'ollama' || !config.embedding_model?.trim()) {
      validation = null;
      return;
    }
    validating = true;
    try {
      validation = await client.validateAiModel('ollama', config.embedding_model.trim());
    } catch {
      validation = null;
    } finally {
      validating = false;
    }
  }

  // #19: click a model name to copy it, with a brief "copied" confirmation.
  async function copyModelName(name: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(name);
      copiedModel = name;
      window.setTimeout(() => {
        if (copiedModel === name) copiedModel = '';
      }, 1500);
    } catch {
      message = name;
    }
  }

  // #5: poll the pull job BY ID (reliable — scanning the size-limited Jobs list missed the finished
  // job once many jobs had accumulated, so the spinner never stopped). Updates a status label, the
  // byte progress bar, and (on failure) the real error. 'missing' = finished + expired from Redis.
  async function pollPull(jobId: string): Promise<void> {
    pullPolling = true;
    pullJobStatus = 'queued';
    pullProgress = null;
    pullError = '';
    try {
      // ~1 h ceiling at 2 s/poll — big models take a while; the loop just stops updating after that.
      for (let i = 0; i < 1800; i += 1) {
        const r = await client.getJobResult(jobId).catch(() => null);
        if (r) {
          const done = r.status === 'finished' || r.status === 'failed' || r.status === 'missing';
          pullJobStatus = r.status === 'missing' ? 'finished' : r.status;
          pullProgress =
            r.progress_total && r.progress_total > 0
              ? { done: r.progress_done ?? 0, total: r.progress_total }
              : pullProgress;
          if (done) {
            if (r.status === 'failed') {
              pullError = r.error ?? 'Pull failed (no error text).';
            } else {
              models = await client.listAiModels().then((x) => x.models);
              pullProgress = null;
              if (searchDone) void doSearch(); // refresh the "pulled ✓" flags in search results
            }
            return;
          }
        }
        await new Promise((res) => window.setTimeout(res, 2000));
      }
    } finally {
      pullPolling = false;
    }
  }

  function pct(p: { done: number; total: number }): string {
    return p.total ? `${Math.floor((p.done / p.total) * 100)}%` : '';
  }

  // #5: search the model catalog (curated + best-effort ollama.com), popularity-ranked.
  async function doSearch(): Promise<void> {
    searching = true;
    try {
      searchResults = (await client.searchAiModels(searchQuery.trim())).models;
      searchDone = true;
    } catch (error) {
      message = errorMessage(error);
      searchResults = [];
    } finally {
      searching = false;
    }
  }

  // Pull a model chosen from the search results (all catalog models are Ollama).
  async function pullFromSearch(name: string): Promise<void> {
    pullProvider = 'ollama';
    pullModel = name;
    await pull();
  }

  // --- Mount / unmount (VRAM control, #5) --------------------------------------------------------

  // Ollama /api/tags names carry ':latest'; treat an untagged name as its ':latest' tag.
  function sameModel(a: string | null | undefined, b: string | null | undefined): boolean {
    if (!a || !b) return false;
    const norm = (s: string) => (s.includes(':') ? s : `${s}:latest`);
    return norm(a) === norm(b);
  }
  function isLoaded(name: string | null | undefined): boolean {
    return !!name && loaded.some((m) => sameModel(m.name, name));
  }
  // A loaded model's memory in GB: actual VRAM when on a GPU, else its resident size.
  function loadedGb(m: LoadedModel): number {
    const v = m.size_vram_bytes && m.size_vram_bytes > 0 ? m.size_vram_bytes : (m.size_bytes ?? 0);
    return v / 1e9;
  }
  function loadedTotalGb(): number {
    return loaded.reduce((s, m) => s + loadedGb(m), 0);
  }
  // Estimated GB to run a pulled model (from its list row's vram_gb).
  function modelEstGb(name: string): number | null {
    const m = models.find((x) => sameModel(x.name, name) || x.name === name);
    return m?.vram_gb ?? null;
  }

  // Running/queued AI jobs whose task labels imply a model — mount/unmount may disrupt them.
  async function runningAiJobs(): Promise<string[]> {
    const q = await client.getJobs(50).catch(() => null);
    if (!q) return [];
    const AI = /embed|reindex|summ|recommend|keyword|topic|model-pull|lexical/i;
    return q.jobs
      .filter((j) => (j.status === 'started' || j.status === 'queued') && AI.test(j.task))
      .map((j) => j.task);
  }

  // True while a mount/unmount job is queued/running — used to disable the mount controls.
  $: modelJobActive = modelJobPolling;

  // Poll the mount/unmount background job to completion, then refresh the true state. On a GPU mount
  // that landed on CPU, explain why (the usual cause is the Ollama container lacking GPU access).
  async function pollModelJob(
    jobId: string,
    verb: string,
    model: string,
    kind: 'summary' | 'embedding',
    compute: 'auto' | 'gpu' | 'cpu',
  ): Promise<void> {
    modelJobPolling = true;
    modelJobId = jobId;
    modelJobVerb = verb;
    modelJobStatus = 'queued';
    modelJobError = '';
    try {
      for (let i = 0; i < 900; i += 1) {
        // Poll BY ID (not the size-limited Jobs list) so the terminal state is always detected.
        const r = await client.getJobResult(jobId).catch(() => null);
        if (r) {
          const done = r.status === 'finished' || r.status === 'failed' || r.status === 'missing';
          const failed = r.status === 'failed';
          modelJobStatus = r.status === 'missing' ? 'finished' : r.status;
          if (done) {
            if (failed) modelJobError = r.error ?? `${verb} failed.`;
            await refreshLive();
            if (verb === 'Mounting' && !failed) {
              const m = loaded.find((x) => sameModel(x.name, model));
              const onGpu = !!(m && m.size_vram_bytes && m.size_vram_bytes > 0);
              if (compute === 'gpu' && !onGpu) {
                message =
                  `Mounted ${model} on CPU — GPU was requested but no VRAM is in use. The Ollama ` +
                  `container likely has no GPU access; grant it (export OLLAMA_GPU=1 + make up-ai) to use the GPU.`;
              } else {
                message = `Mounted ${model} — active ${kind} model (${m ? placementLabel(m) : 'loaded'}).`;
              }
            } else if (verb === 'Unmounting' && !failed) {
              message = `Unmounted ${model} — this capability now uses its built-in baseline.`;
            }
            return;
          }
        }
        await new Promise((res) => window.setTimeout(res, 2000));
      }
    } finally {
      modelJobPolling = false;
    }
  }

  // Confirm the mount is safe (VRAM budget headroom + running AI jobs), then enqueue it (background).
  async function mount(model: string | null, kind: 'summary' | 'embedding', estGb?: number | null): Promise<void> {
    const name = (model ?? '').trim();
    if (!name || modelJobPolling) return;
    const budget = config?.vram_budget_gb ?? null;
    const est = estGb ?? modelEstGb(name);
    if (budget && est) {
      const other = loaded.filter((m) => !sameModel(m.name, name)).reduce((s, m) => s + loadedGb(m), 0);
      if (est + other > budget) {
        const ok = window.confirm(
          `Mounting ${name} needs ~${est.toFixed(1)} GB and ${other.toFixed(1)} GB is already loaded — ` +
            `that may exceed your ${budget} GB budget. Mount anyway?`,
        );
        if (!ok) return;
      }
    }
    const running = await runningAiJobs();
    if (running.length) {
      const ok = window.confirm(
        `${running.length} AI job(s) are running (${running.join(', ')}). Mounting a different model ` +
          `may cause them to fail or use the wrong model. Continue?`,
      );
      if (!ok) return;
    }
    await run(async () => {
      const r = await client.mountAiModel(name, kind, mountCompute);
      message = `Mount queued for ${name} (${mountCompute}).`;
      void pollModelJob(r.job_id, 'Mounting', name, kind, mountCompute);
    });
  }

  async function unmount(model: string | null, kind: 'summary' | 'embedding'): Promise<void> {
    const name = (model ?? '').trim();
    if (!name || modelJobPolling) return;
    const running = await runningAiJobs();
    if (running.length) {
      const ok = window.confirm(
        `${running.length} AI job(s) are running (${running.join(', ')}). Unmounting will drop this ` +
          `capability to its baseline and may fail those jobs. Continue?`,
      );
      if (!ok) return;
    }
    await run(async () => {
      const r = await client.unmountAiModel(name, kind);
      message = `Unmount queued for ${name}.`;
      void pollModelJob(r.job_id, 'Unmounting', name, kind, 'auto');
    });
  }

  // Unmount a model shown in the "loaded" list: infer its kind from the active config, else its name.
  async function unmountLoaded(m: LoadedModel): Promise<void> {
    let kind: 'summary' | 'embedding';
    if (config && config.embedding_provider === 'ollama' && sameModel(config.embedding_model, m.name)) {
      kind = 'embedding';
    } else if (config && config.summary_provider === 'local_llm' && sameModel(config.summary_model, m.name)) {
      kind = 'summary';
    } else {
      kind = /embed/i.test(m.name) ? 'embedding' : 'summary';
    }
    await unmount(m.name, kind);
  }

  // A loaded model is "pinned" (mounted with keep_alive=-1) when its expiry is far in the future; a
  // model auto-loaded to serve a request expires within minutes.
  function isPinned(m: LoadedModel): boolean {
    if (!m.expires_at) return false;
    const t = Date.parse(m.expires_at);
    return !Number.isNaN(t) && t - Date.now() > 24 * 3600 * 1000;
  }
  function autoExpiresLabel(m: LoadedModel): string {
    if (!m.expires_at) return 'auto';
    const mins = Math.max(0, Math.round((Date.parse(m.expires_at) - Date.now()) / 60000));
    return Number.isNaN(mins) ? 'auto' : `auto · frees in ~${mins}m`;
  }
  // GPU vs CPU placement from the actual VRAM in use (size_vram > 0 ⇒ on GPU).
  function placementLabel(m: LoadedModel): string {
    if (m.size_vram_bytes && m.size_vram_bytes > 0) {
      const partial = m.size_bytes && m.size_vram_bytes < m.size_bytes;
      return `GPU${partial ? ' (partial)' : ''} · ${(m.size_vram_bytes / 1e9).toFixed(1)} GB VRAM`;
    }
    return `CPU${m.size_bytes ? ` · ${fmtSize(m.size_bytes)} RAM` : ''}`;
  }

  // Availability + how-to-enable note for a specific provider/backend option, read from the
  // detected providers map. Unknown keys default to "available" (the dependency-free baseline).
  function avail(group: 'embedding' | 'summary' | 'topic' | 'extraction', key: string): boolean {
    return status?.providers?.[group]?.[key]?.available ?? true;
  }
  function note(group: 'embedding' | 'summary' | 'topic' | 'extraction', key: string): string | null {
    return status?.providers?.[group]?.[key]?.note ?? null;
  }

  // A card's status badge: green when the active selection runs as chosen; amber when it silently
  // falls back to the built-in baseline; the reason is the provider note.
  type Badge = { kind: 'ok' | 'baseline' | 'off'; label: string; reason: string | null };

  function embeddingBadge(): Badge {
    if (!status || !config) return { kind: 'baseline', label: 'Loading…', reason: null };
    const a = status.active.embedding;
    if (config.embedding_provider === 'hash_bow') {
      return { kind: 'baseline', label: 'Built-in baseline', reason: 'Dependency-free hashed bag-of-words — always works.' };
    }
    if (a.available) return { kind: 'ok', label: 'Available', reason: null };
    return { kind: 'off', label: 'Falls back to hash-BOW', reason: a.note };
  }

  function summaryBadge(): Badge {
    if (!status || !config) return { kind: 'baseline', label: 'Loading…', reason: null };
    const a = status.active.summary;
    if (config.summary_provider === 'extractive') {
      return { kind: 'baseline', label: 'Built-in baseline', reason: 'Dependency-free extractive summarizer — always works.' };
    }
    if (a.available) return { kind: 'ok', label: 'Available', reason: null };
    return { kind: 'off', label: 'Falls back to extractive', reason: a.note };
  }

  function topicBadge(): Badge {
    if (!config) return { kind: 'baseline', label: 'Loading…', reason: null };
    // Every topic backend is the built-in deterministic TF-IDF model today; be honest about it.
    return {
      kind: 'baseline',
      label: 'Built-in baseline',
      reason: 'Deterministic TF-IDF topic model — always works, no downloads or network.',
    };
  }

  function ocrBadge(): Badge {
    if (!config) return { kind: 'baseline', label: 'Loading…', reason: null };
    if (config.ocr_backend === 'none') {
      return { kind: 'baseline', label: 'OCR off', reason: 'GROBID runs on the PDF as-is — scanned pages stay un-searchable.' };
    }
    const backend = config.ocr_backend;
    const ok = avail('extraction', backend);
    const reason =
      backend === 'pymupdf'
        ? 'PyMuPDF + tesseract adds a text layer to scanned/poor-text PDFs before GROBID.'
        : 'OCRmyPDF adds a text layer to scanned/poor-text PDFs before GROBID.';
    return ok
      ? { kind: 'ok', label: 'Available', reason }
      : { kind: 'off', label: 'OCR unavailable', reason: note('extraction', backend) };
  }

  // True when a topic backend that *sounds* like it uses embeddings/BERTopic is selected, so we
  // can show the honesty banner explaining it is actually the TF-IDF stand-in.
  $: topicPretendsAdvanced =
    config != null && (config.topic_backend === 'bertopic' || config.topic_backend === 'embedding');

  // Recompute the per-card badges whenever config/status change (referenced in the markup, where
  // {@const} isn't allowed directly under a plain <article>). Each badge fn reads status/config, so
  // naming them here makes Svelte re-run the block on any change.
  let embBadge: Badge = { kind: 'baseline', label: 'Loading…', reason: null };
  let sumBadge: Badge = { kind: 'baseline', label: 'Loading…', reason: null };
  let topBadge: Badge = { kind: 'baseline', label: 'Loading…', reason: null };
  let ocrBdg: Badge = { kind: 'baseline', label: 'Loading…', reason: null };
  $: if (status || config) {
    embBadge = embeddingBadge();
    sumBadge = summaryBadge();
    topBadge = topicBadge();
    ocrBdg = ocrBadge();
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
    const name = pullModel.trim();
    await run(async () => {
      const r = await client.pullAiModel(pullProvider, name);
      message = `Pull queued for ${pullProvider}/${name} (job ${r.job_id.slice(0, 8)}).`;
      pullModel = '';
      pullJobId = r.job_id;
      // #5: track progress by polling the Jobs status (background pull, no streaming endpoint).
      void pollPull(r.job_id);
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

  async function doRebuildLexical(): Promise<void> {
    await run(async () => {
      const r = await client.rebuildLexicalIndex();
      message =
        r.status === 'queued'
          ? `Lexical index rebuild queued (job ${(r.job_id ?? '').slice(0, 8)}).`
          : 'Lexical index rebuilt.';
      status = await client.getAiStatus();
    });
  }

  function fmtSize(bytes: number | null): string {
    if (!bytes) return '';
    const gb = bytes / 1e9;
    return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / 1e6).toFixed(0)} MB`;
  }

  // The Ollama models actually pulled locally — the option pool for the embedding/summary model
  // dropdowns (the common ollama / local_llm case).
  $: ollamaModelNames = models.filter((m) => m.provider === 'ollama').map((m) => m.name);

  // Keep an already-configured model selectable even when it isn't among the currently-pulled
  // options, so saving never silently drops a saved value.
  function withCurrent(options: string[], current: string | null | undefined): string[] {
    const c = (current ?? '').trim();
    return c && !options.includes(c) ? [...options, c] : options;
  }

  $: embeddingModelOptions = withCurrent(
    ollamaModelNames,
    config?.embedding_provider === 'ollama' ? config?.embedding_model : '',
  );
  $: summaryModelOptions = withCurrent(
    ollamaModelNames,
    config?.summary_provider === 'local_llm' ? config?.summary_model : '',
  );
</script>

<section class="card">
  <div class="card-head">
    <h2>AI &amp; Models</h2>
    <button type="button" class="help-btn" on:click={() => (showHelp = true)}
      title="Open a detailed guide to every option on this page">? Help</button>
  </div>
  <p class="muted">
    Each capability below picks the engine used for one AI feature. The dependency-free baselines
    (hashed bag-of-words / extractive summaries / TF-IDF topics) always work; heavier engines need
    an extra dependency or a reachable Ollama server, and each control tells you what's missing when
    it can't run. Nothing here is ever fully off — an unavailable engine simply degrades to the
    built-in baseline.
  </p>
  {#if message}<p class="message">{message}</p>{/if}

  {#if config && status}
    <div class="cards">
      <!-- Semantic search & related papers (embeddings) -->
      <article class="cap">
        <header>
          <h3>Semantic search &amp; related papers</h3>
          <span class="badge badge-{embBadge.kind}" title={embBadge.reason ?? ''}>{embBadge.label}</span>
        </header>
        <p class="what">Turns each paper into a vector so search and "related papers" can match by meaning, not just keywords.</p>
        <p class="used">Used for: the semantic search box and the related-papers list on a paper. Changing the model re-embeds every paper (queues a reindex).</p>
        {#if embBadge.reason}<p class="reason">{embBadge.reason}</p>{/if}
        <label>Embedding provider
          <select bind:value={config.embedding_provider} disabled={busy}
            title="Engine used to embed papers for semantic search & related papers">
            {#each status.allowed.embedding_provider ?? [] as p}
              <option value={p} disabled={!avail('embedding', p)}
                title={avail('embedding', p) ? '' : (note('embedding', p) ?? 'Not available in this deployment')}>
                {p}{avail('embedding', p) ? '' : ' (unavailable)'}
              </option>
            {/each}
          </select>
          {#if note('embedding', config.embedding_provider)}
            <small class="hint">{note('embedding', config.embedding_provider)}</small>
          {/if}
        </label>
        {#if config.embedding_provider === 'ollama'}
          <label>Embedding model
            <select bind:value={config.embedding_model} on:change={validateEmbedding} disabled={busy}
              title="Pulled Ollama embedding model to use for semantic search">
              <option value="">(provider default)</option>
              {#each embeddingModelOptions as name}
                <option value={name}>{name}</option>
              {/each}
            </select>
            <small class="hint">Pick a pulled Ollama model, or add one with “Pull model” below (e.g. nomic-embed-text).</small>
          </label>
          <!-- #5: mount = load into memory + make active; unmount = free memory + baseline fallback. -->
          <div class="mount-row">
            {#if isLoaded(config.embedding_model)}
              <span class="loaded-dot" title="Loaded in the Ollama daemon's memory">● loaded</span>
              <button type="button" class="secondary small" on:click={() => unmount(config.embedding_model, 'embedding')}
                disabled={busy || modelJobActive} title="Free this model from memory; semantic search falls back to hash-BOW">Unmount</button>
            {:else}
              <button type="button" class="small" on:click={() => mount(config.embedding_model, 'embedding')}
                disabled={busy || modelJobActive || !config.embedding_model?.trim()}
                title={config.embedding_model?.trim() ? 'Load this model into memory (using the Compute choice below) and use it for semantic search' : 'Pick a model first'}>Mount</button>
            {/if}
          </div>
        {:else if config.embedding_provider === 'sentence_transformers'}
          <label>Embedding model
            <select disabled title="sentence-transformers is not installed in this image">
              <option>{config.embedding_model || '—'}</option>
            </select>
            <small class="hint">
              {avail('embedding', 'sentence_transformers')
                ? 'No runtime model list — manage the weights via “Pull model” below.'
                : 'sentence-transformers is not installed in this image.'}
            </small>
          </label>
        {:else}
          <label>Embedding model
            <select disabled title="No model needed for the built-in hashed bag-of-words baseline">
              <option>—</option>
            </select>
            <small class="hint">No model needed for this provider.</small>
          </label>
        {/if}
        <!-- #2: validate the Ollama embedding model + show the resolved effective model. -->
        {#if config.embedding_provider === 'ollama' && config.embedding_model?.trim()}
          {#if validating}
            <p class="reason">Validating model…</p>
          {:else if validation}
            {#if validation.present === false}
              <p class="banner" title="The model isn't pulled on the Ollama server">
                Not pulled on Ollama — run “Pull model” below (or <code>ollama pull {config.embedding_model.trim()}</code>).
              </p>
            {:else if validation.embeddings === false}
              <p class="banner" title="This model doesn't produce embeddings">
                Not an embedding model — pick a model that supports embeddings (e.g. nomic-embed-text).
              </p>
            {:else if validation.present === null || validation.embeddings === null}
              <p class="banner" title="Couldn't reach the Ollama daemon to validate">
                Couldn't validate — the Ollama daemon is unreachable{validation.error ? ` (${validation.error})` : ''}.
              </p>
            {/if}
            {#if validation.canonical}
              <p class="reason">Effective model: <code>{validation.canonical}</code></p>
            {/if}
          {/if}
        {/if}
      </article>

      <!-- Topic modeling (topic backend) -->
      <article class="cap">
        <header>
          <h3>Topic modeling</h3>
          <span class="badge badge-{topBadge.kind}" title={topBadge.reason ?? ''}>{topBadge.label}</span>
        </header>
        <p class="what">Clusters a scope's papers into topics and labels each with its top terms.</p>
        <p class="used">Used for: the Topics view in Insights, and turning a topic into a tag or a shelf.</p>
        {#if topBadge.reason}<p class="reason">{topBadge.reason}</p>{/if}
        {#if topicPretendsAdvanced}
          <p class="banner" title="BERTopic is a heavy optional dependency and is not installed here">
            BERTopic isn't installed; this uses the built-in TF-IDF topic model. Results are the
            same as “tfidf”.
          </p>
        {/if}
        <label>Topic backend
          <select bind:value={config.topic_backend} disabled={busy}
            title="How papers are clustered into topics (all options are the built-in TF-IDF model today)">
            {#each status.allowed.topic_backend ?? [] as p}
              <option value={p} disabled={!avail('topic', p)}
                title={note('topic', p) ?? ''}>
                {p}{avail('topic', p) ? '' : ' (unavailable)'}
              </option>
            {/each}
          </select>
          {#if note('topic', config.topic_backend)}
            <small class="hint">{note('topic', config.topic_backend)}</small>
          {/if}
        </label>
      </article>

      <!-- Scope summaries (summary provider) -->
      <article class="cap">
        <header>
          <h3>Scope summaries</h3>
          <span class="badge badge-{sumBadge.kind}" title={sumBadge.reason ?? ''}>{sumBadge.label}</span>
        </header>
        <p class="what">Generates a short prose summary of a paper, shelf or rack.</p>
        <p class="used">Used for: the "Summarize" action on a paper and the scope summaries in Insights.</p>
        {#if sumBadge.reason}<p class="reason">{sumBadge.reason}</p>{/if}
        <label>Summary provider
          <select bind:value={config.summary_provider} disabled={busy} title="Engine used to generate summaries">
            {#each status.allowed.summary_provider ?? [] as p}
              <option value={p} disabled={!avail('summary', p)}
                title={avail('summary', p) ? '' : (note('summary', p) ?? 'Not available in this deployment')}>
                {p}{avail('summary', p) ? '' : ' (unavailable)'}
              </option>
            {/each}
          </select>
          {#if note('summary', config.summary_provider)}
            <small class="hint">{note('summary', config.summary_provider)}</small>
          {/if}
        </label>
        {#if config.summary_provider === 'local_llm'}
          <label>Summary model (Ollama)
            <select bind:value={config.summary_model} disabled={busy}
              title="Pulled Ollama model used to generate summaries">
              <option value="">(provider default)</option>
              {#each summaryModelOptions as name}
                <option value={name}>{name}</option>
              {/each}
            </select>
            <small class="hint">Pick a pulled Ollama model, or add one with “Pull model” below.</small>
          </label>
          <!-- #5: mount = load into memory + make active; unmount = free memory + baseline fallback. -->
          <div class="mount-row">
            {#if isLoaded(config.summary_model)}
              <span class="loaded-dot" title="Loaded in the Ollama daemon's memory">● loaded</span>
              <button type="button" class="secondary small" on:click={() => unmount(config.summary_model, 'summary')}
                disabled={busy || modelJobActive} title="Free this model from memory; summaries fall back to the extractive baseline">Unmount</button>
            {:else}
              <button type="button" class="small" on:click={() => mount(config.summary_model, 'summary')}
                disabled={busy || modelJobActive || !config.summary_model?.trim()}
                title={config.summary_model?.trim() ? 'Load this model into memory (using the Compute choice below) and use it for summaries' : 'Pick a model first'}>Mount</button>
            {/if}
          </div>
        {:else}
          <label>Summary model
            <select disabled title="No model needed for the built-in extractive summarizer">
              <option>{config.summary_model || '—'}</option>
            </select>
            <small class="hint">No model needed for this provider.</small>
          </label>
        {/if}
      </article>

      <!-- Keyword extraction (read-only, always available) -->
      <article class="cap">
        <header>
          <h3>Keyword extraction</h3>
          <span class="badge badge-baseline" title="Built-in RAKE extractor — no dependencies, always available">Built-in baseline</span>
        </header>
        <p class="what">Pulls representative keyword phrases from a paper's title and abstract.</p>
        <p class="used">Used for: the keyword chips on a paper and keyword search.</p>
        <p class="reason">Always on — a dependency-free RAKE extractor with no settings to configure.</p>
      </article>

      <!-- PDF text extraction / OCR (ocr_backend) -->
      <article class="cap">
        <header>
          <h3>PDF text extraction / OCR</h3>
          <span class="badge badge-{ocrBdg.kind}" title={ocrBdg.reason ?? ''}>{ocrBdg.label}</span>
        </header>
        <p class="what">Adds a searchable text layer to scanned or poor-text PDFs before extraction, so GROBID can read them.</p>
        <p class="used">Used for: extracting metadata, abstract, keywords and references from a paper's PDF. OCR runs locally on the stored file (no network).</p>
        {#if ocrBdg.reason}<p class="reason">{ocrBdg.reason}</p>{/if}
        <label>Extraction backend
          <select bind:value={config.ocr_backend} disabled={busy}
            title="How PDF text is extracted before GROBID (OCRmyPDF or PyMuPDF adds a searchable text layer to scanned/poor-text PDFs)">
            {#each status.allowed.ocr_backend ?? [] as p}
              <option value={p} disabled={!avail('extraction', p)}
                title={avail('extraction', p) ? (note('extraction', p) ?? '') : (note('extraction', p) ?? 'Not available in this deployment')}>
                {p}{avail('extraction', p) ? '' : ' (unavailable)'}
              </option>
            {/each}
          </select>
          {#if note('extraction', config.ocr_backend)}
            <small class="hint">{note('extraction', config.ocr_backend)}</small>
          {/if}
        </label>
        {#if config.ocr_backend === 'ocrmypdf' || config.ocr_backend === 'pymupdf'}
          <label>OCR languages (tesseract codes, e.g. eng+spa)
            <input bind:value={config.ocr_language} placeholder="eng" disabled={busy}
              title="Tesseract language codes used for OCR; combine several with '+' (e.g. eng+spa)" />
          </label>
        {/if}
      </article>
    </div>

    <div class="row shared">
      <button type="button" on:click={save} disabled={busy}
        title="Save the AI provider/model settings (changing the embedding model queues a reindex)">Save config</button>
      <button type="button" class="secondary" on:click={refresh} disabled={busy}
        title="Reload the current settings and provider availability">Refresh</button>
      <label class="ollama-url">Ollama URL
        <input bind:value={config.ollama_url} placeholder="http://localhost:11434" disabled={busy}
          title="Base URL of the Ollama server used by the ollama / local_llm engines" />
      </label>
      <label class="vram-budget">Memory budget (GB)
        <input type="number" min="0" step="0.5" bind:value={config.vram_budget_gb} placeholder="e.g. 8" disabled={busy}
          title="VRAM/RAM budget for the Ollama host; mounting warns before it would be exceeded. Save to persist." />
      </label>
      <!-- #5: alive-and-reachable semaphore (green/red), like the Jobs-tab dot. Reflects the daemon
           at the configured Ollama URL; its tooltip carries the URL + version for quick debugging. -->
      <span class="ollama-sema"
        title={status.ollama_reachable
          ? `Ollama reachable at ${config.ollama_url}${status.ollama_version ? ` (v${status.ollama_version})` : ''}`
          : `Ollama unreachable at ${config.ollama_url} — start it (make up-ai) or fix the Ollama URL. Pulls/embeds run in the worker, which must reach this URL too.`}>
        <span class="sema-dot sema-{status.ollama_reachable ? 'green' : 'red'}" aria-hidden="true"></span>
        Ollama {status.ollama_reachable
          ? `reachable${status.ollama_version ? ` · v${status.ollama_version}` : ''}`
          : 'unreachable'}
      </span>
    </div>

    <!-- #5: models currently held in the Ollama daemon's memory. -->
    <h3 class="section">Loaded in memory</h3>
    <p class="muted small">
      Mounting a model (via the <em>Mount</em> button on the “Semantic search” or “Scope summaries”
      card above) pins it in the Ollama daemon's memory and makes it the active model for its
      capability; unmounting frees the memory and that capability falls back to its built-in baseline.
      Only one model per kind is active at a time.{config.vram_budget_gb
        ? ` Budget ${config.vram_budget_gb} GB · loaded now ${loadedTotalGb().toFixed(1)} GB.`
        : ''}
    </p>
    <div class="row">
      <label class="compute">Compute for next mount
        <select bind:value={mountCompute} disabled={busy || modelJobActive}
          title="How Ollama loads the model: Auto lets the daemon decide; GPU offloads all layers to the GPU (if one is available to the container); CPU forces it into system RAM.">
          <option value="auto">Auto (daemon decides)</option>
          <option value="gpu">Prefer GPU</option>
          <option value="cpu">Force CPU (RAM)</option>
        </select>
      </label>
    </div>
    <!-- #5: mount/unmount run as background jobs; show live status + real error. -->
    {#if modelJobId}
      <div class="pull-status" class:failed={modelJobStatus === 'failed'} role="status">
        <p class="pull-line">
          {#if modelJobPolling}<span class="spinner" aria-hidden="true"></span>{/if}
          {modelJobVerb} {modelJobStatus || 'queued'}
          {#if modelJobStatus === 'finished'}✓{:else if modelJobStatus === 'failed'}✗{/if}
          <small class="muted">(job {modelJobId.slice(0, 8)})</small>
        </p>
        {#if modelJobError}<p class="pull-err">{modelJobError}</p>{/if}
      </div>
    {/if}
    {#if loaded.length === 0}
      <p class="empty">Nothing loaded. Mount a model from the “Semantic search” or “Scope summaries” card above.</p>
    {:else}
      <ul class="models">
        {#each loaded as m (m.name)}
          <li>
            <span>
              <strong>{m.name}</strong>
              <small class="muted">{placementLabel(m)}</small>
              {#if isPinned(m)}
                <span class="pin-badge" title="Pinned in memory (mounted). Stays until you unmount it.">mounted</span>
              {:else}
                <span class="auto-badge" title="Loaded on demand to serve a request (e.g. embedding a search query). Not pinned — Ollama frees it automatically after a few idle minutes. This is not a mount.">{autoExpiresLabel(m)}</span>
              {/if}
            </span>
            <button type="button" class="secondary small" on:click={() => unmountLoaded(m)} disabled={busy || modelJobActive}
              title="Free this model from memory now">Unmount</button>
          </li>
        {/each}
      </ul>
    {/if}

    <h3 class="section">Models</h3>
    <p class="muted small">
      Download the weights the heavier engines need. Pulling <code>ollama</code> models needs the
      Ollama profile running (<code>make up-ai</code>); pulling <code>sentence_transformers</code>
      downloads the weights into the Hugging Face cache and needs that package installed in the
      image.
    </p>

    <!-- #5: model search — Ollama has no search API/VRAM, so results are a curated catalog plus a
         best-effort ollama.com lookup, popularity-ranked, with an *estimated* VRAM need. -->
    <div class="row">
      <input bind:value={searchQuery} placeholder="Find a model (e.g. qwen, embed, llama)" disabled={searching}
        on:keydown={(e) => { if (e.key === 'Enter') doSearch(); }}
        title="Search popular models by name or keyword" />
      <button type="button" class="secondary" on:click={doSearch} disabled={searching}
        title="Search the model catalog (popular models, sizes and estimated VRAM)">
        {searching ? 'Searching…' : 'Search'}
      </button>
    </div>
    {#if searchDone && !searching}
      {#if searchResults.length === 0}
        <p class="empty">No matches. Try a broader term, or type an exact name in “Pull model” below.</p>
      {:else}
        <p class="muted small">
          VRAM is a rough estimate (quantized weights + overhead) — a sizing guide, not a guarantee.
          Sizes are approximate; <code>ollama.com</code> hits show size/VRAM only once pulled.
        </p>
        <div class="catalog-wrap">
          <table class="catalog">
            <thead>
              <tr><th>Model</th><th>Type</th><th>Size</th><th>Est. VRAM</th><th>Popularity</th><th></th></tr>
            </thead>
            <tbody>
              {#each searchResults as r (r.name)}
                <tr>
                  <td>
                    <button type="button" class="copy-name" on:click={() => copyModelName(r.name)}
                      title="Click to copy this model name">{r.name}</button>
                    {#if copiedModel === r.name}<span class="copied">copied ✓</span>{/if}
                    {#if r.source === 'ollama.com'}<span class="src-badge" title="Live result from ollama.com">ollama.com</span>{/if}
                    {#if r.blurb}<small class="muted blurb">{r.blurb}</small>{/if}
                  </td>
                  <td>{r.kind === 'embedding' ? 'embedding' : 'LLM'}</td>
                  <td>{r.size_bytes ? fmtSize(r.size_bytes) : '—'}</td>
                  <td>{r.vram_gb != null ? `~${r.vram_gb} GB` : '—'}</td>
                  <td><span class="pop" style="--pop:{r.popularity}%" title="{r.popularity}/100"></span></td>
                  <td>
                    {#if r.pulled}
                      <span class="copied">pulled ✓</span>
                    {:else}
                      <button type="button" class="secondary small" on:click={() => pullFromSearch(r.name)}
                        disabled={busy} title="Download this model in the background">Pull</button>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/if}

    <p class="muted small">Or pull by exact name:</p>
    <div class="row">
      <select bind:value={pullProvider} disabled={busy} title="Provider to download the model from">
        <option value="ollama">ollama</option>
        <option value="sentence_transformers"
          disabled={status.sentence_transformers_installed === false}
          title={status.sentence_transformers_installed === false
            ? 'sentence-transformers is not installed in this image — rebuild with the AI extra'
            : 'Download sentence-transformers weights into the HF cache'}>
          sentence_transformers{status.sentence_transformers_installed === false ? ' (not installed)' : ''}
        </option>
      </select>
      <input bind:value={pullModel} placeholder="model name (e.g. nomic-embed-text)" disabled={busy}
        title="Name of the model to download" />
      <button type="button" class="secondary" on:click={pull} disabled={busy || !pullModel.trim()}
        title={pullModel.trim() ? 'Download this model in the background' : 'Enter a model name first'}>
        Pull model
      </button>
    </div>
    <!-- #5: live pull progress + real error text via Jobs polling. -->
    {#if pullJobId}
      <div class="pull-status" class:failed={pullJobStatus === 'failed'} role="status">
        <p class="pull-line">
          {#if pullPolling}<span class="spinner" aria-hidden="true"></span>{/if}
          Pull {pullJobStatus || 'queued'}
          {#if pullJobStatus === 'finished'}✓{:else if pullJobStatus === 'failed'}✗{/if}
          <small class="muted">(job {pullJobId.slice(0, 8)})</small>
        </p>
        {#if pullProgress}
          <progress max={pullProgress.total} value={pullProgress.done}></progress>
          <small class="muted">{pct(pullProgress)} — {fmtSize(pullProgress.done)} / {fmtSize(pullProgress.total)}</small>
        {/if}
        {#if pullError}<p class="pull-err">{pullError}</p>{/if}
      </div>
    {/if}
    {#if models.length === 0}
      <p class="empty">No models pulled yet — search &amp; pull one above (needs the Ollama profile running). Pulled models appear here, each with a Delete button.</p>
    {:else}
      <ul class="models">
        {#each models as m (m.provider + m.name)}
          <li>
            <span>
              <!-- #19: click the model name to copy it. -->
              <button type="button" class="copy-name" on:click={() => copyModelName(m.name)}
                title="Click to copy this model name">{m.name}</button>
              {#if copiedModel === m.name}<span class="copied">copied ✓</span>{/if}
              <small class="muted">{m.provider} {fmtSize(m.size_bytes)}{m.vram_gb != null ? ` · ~${m.vram_gb} GB to run` : ''}</small>
            </span>
            <button type="button" class="secondary small" on:click={() => remove(m)} disabled={busy} title="Delete this downloaded model (also drops its embedding registration)">Delete</button>
          </li>
        {/each}
      </ul>
    {/if}

    <!-- #21: registered embedding models + the model cap. -->
    <h3 class="section" title="Embedding models that have a stored vector column and can be used for semantic search right now">Registered embedding models</h3>
    <p class="muted small">
      An embedding model becomes <em>registered</em> the first time you index papers with it (select it
      as the Embedding model above and <strong>Save</strong>, or <strong>Mount</strong> it — either
      queues a reindex that builds and stores a vector column for that model). Registered models keep
      their vectors even when they aren't the active one, so you can switch between them without
      re-indexing. The Search tab can rank with any registered model, or fuse them all (“Multimode”,
      Reciprocal-Rank-Fusion){maxModels ? `. Up to ${maxModels} models can be registered at once` : ''}.
      Deleting a model (above) also drops its registration and frees a slot.
    </p>
    {#if embeddingModels.length === 0}
      <p class="empty">No embedding models registered.</p>
    {:else}
      <ul class="models">
        {#each embeddingModels as m (m.model_name)}
          <li class:unavailable={m.available === false}>
            <span>
              <button type="button" class="copy-name" on:click={() => copyModelName(m.model_name)}
                title="Click to copy this model name">{m.model_name}</button>
              {#if copiedModel === m.model_name}<span class="copied">copied ✓</span>{/if}
              <small class="muted">{m.provider} · dim {m.dim}</small>
              {#if m.available === false}
                <span class="unavail-badge" title="This model's provider isn't installed in this image, so it can't be selected.">unavailable — provider not installed</span>
              {/if}
            </span>
          </li>
        {/each}
      </ul>
    {/if}

    <h3 class="section" title="Coverage of the stored vectors that power semantic search">Embedding index</h3>
    <p class="muted small">
      Semantic search compares a stored numeric <em>vector</em> (embedding) per paper. This shows how
      many papers already have a vector for the active model. <strong>Reindex embeddings</strong>
      (below) recomputes vectors for every paper with the current model — run it after changing the
      embedding provider/model, or if coverage is below 100&nbsp;%, so search results stay consistent.
      It runs in the background (watch the Jobs tab); search keeps working on whatever is already
      indexed meanwhile.
    </p>
    {#if status.reindex}
      <p class="muted">
        <strong>{status.reindex.indexed}</strong> / {status.reindex.total} papers indexed for
        <code>{status.reindex.model_name}</code>.
      </p>
    {/if}
    {#if status.chunk_embeddings}
      <p class="muted small">
        {#if status.chunk_embeddings.column}
          Chunk-level ANN: <strong>{status.chunk_embeddings.indexed}</strong> /
          {status.chunk_embeddings.total} passages embedded in
          <code>{status.chunk_embeddings.column}</code> (used for passage-level semantic + hybrid
          search). Reindexing backfills these too.
        {:else}
          Chunk-level ANN is inactive for the current model — semantic search uses the
          document-level baseline. Select a model with a dedicated vector column (e.g. MiniLM /
          nomic) on Postgres to enable passage retrieval.
        {/if}
      </p>
    {/if}
    {#if status.lexical_index}
      <p class="muted small">
        Lexical (BM25F+) index: {status.lexical_index.loaded
          ? `warm — ${status.lexical_index.docs} papers`
          : 'not yet warmed (builds on first search / library open)'}{status.lexical_index.stale
          ? ' · rebuilding to include recent changes…'
          : ''}.
        It rebuilds automatically when the library changes; use Rebuild to force it now.
      </p>
      <button type="button" class="secondary" on:click={doRebuildLexical} disabled={busy}
        title="Rebuild the lexical (keyword) search index now — normally it refreshes itself when papers change">Rebuild index</button>
    {/if}
    <button type="button" class="secondary" on:click={doReindex} disabled={busy}
      title="Rebuild embeddings for every paper with the current embedding model">Reindex embeddings</button>
  {:else}
    <p class="empty">Loading…</p>
  {/if}
</section>

{#if showHelp}
  <Modal wide title="AI &amp; Models — guide" onClose={() => (showHelp = false)}>
    <div class="help">
      <p class="lead">
        This page controls the engines behind PaRacORD's AI features. Every feature has a
        <strong>dependency-free baseline that always works</strong> (no downloads, no GPU, no
        network); heavier engines (Ollama models) are optional and, when unavailable, silently fall
        back to that baseline — nothing is ever fully off. Below is what each option does, what it
        consumes, and what to expect.
      </p>

      <details open>
        <summary>The five capabilities</summary>
        <dl>
          <dt>Semantic search &amp; related papers</dt>
          <dd>Turns each paper into an <em>embedding</em> (a list of numbers — a “vector” — capturing
            meaning) so search and the “related papers” list can match by <em>meaning</em>, not just
            shared keywords. Baseline: <code>hash-BOW</code> (a dependency-free hashed
            bag-of-words — works, but coarse). Better: an Ollama embedding model such as
            <code>nomic-embed-text</code>.</dd>
          <dt>Scope summaries</dt>
          <dd>Writes a short prose summary of a paper, shelf or rack. Baseline: an extractive
            summarizer (picks the most representative existing sentences). Better: a local LLM via
            Ollama (<code>local_llm</code>), e.g. <code>qwen3</code> / <code>llama3.x</code>.</dd>
          <dt>Topic modeling</dt>
          <dd>Clusters a set of papers into topics and labels each with its top terms. Today every
            backend is the built-in deterministic TF-IDF model (fast, no downloads).</dd>
          <dt>Keyword extraction</dt>
          <dd>Pulls representative keyword phrases from a title + abstract using RAKE (a
            dependency-free algorithm). Always on, nothing to configure.</dd>
          <dt>PDF text extraction / OCR</dt>
          <dd>Adds a searchable text layer to scanned / poor-text PDFs before GROBID reads them.
            Runs locally on the stored file (no network). Needs the OCR tools baked into the image.</dd>
        </dl>
      </details>

      <details>
        <summary>Providers &amp; baselines</summary>
        <p>Each capability picks a <em>provider</em> (the engine). Baselines
          (<code>hash_bow</code>, <code>extractive</code>, <code>tfidf</code>, RAKE) need nothing.
          <code>ollama</code> / <code>local_llm</code> need a reachable Ollama daemon (the green
          semaphore) with the model pulled. <code>sentence_transformers</code> needs that Python
          package baked into the image. If a chosen provider can't run, the badge on its card turns
          amber/red and it falls back to the baseline — your results still appear, just coarser.</p>
      </details>

      <details>
        <summary>Finding, pulling &amp; deleting models</summary>
        <p><strong>Find a model</strong> searches a curated catalog (plus a live lookup on
          <code>ollama.com</code> when reachable) and shows an <em>estimated</em> memory requirement.
          <strong>Pull</strong> downloads the weights in the background (with a live progress bar).
          Pulled models appear under “Models” with their on-disk size, an estimate of the memory to
          run them, and a <strong>Delete</strong> button (which also drops the model's embedding
          registration and frees a slot).</p>
        <p class="note">VRAM/RAM figures are <em>estimates</em> (quantized weights + a working-memory
          allowance). They're a sizing guide, not a guarantee — actual use varies with context length
          and the runtime.</p>
      </details>

      <details>
        <summary>Keeping Ollama up to date</summary>
        <p>The Ollama daemon runs from the <code>ollama/ollama:latest</code> image. Updating it brings
          bug fixes, GPU/performance improvements, and support for newer models (a brand-new model can
          require a recent Ollama). It's worth doing every few weeks, and whenever a pull fails with an
          “unsupported/unknown model” error. Your <strong>pulled models are safe</strong> — they live
          in a Docker volume, not the image, so an update never re-downloads them.</p>
        <p>Because it recreates a container, the update runs on the server, not from this page. On the
          host run:</p>
        <p><code>make ai-update</code></p>
        <p>— which pulls the newest image, recreates the container, and prints the version before/after.
          The current daemon version is shown by the Ollama semaphore's tooltip at the top of this
          page.</p>
      </details>

      <details>
        <summary>Mounting, unmounting &amp; memory (VRAM)</summary>
        <p><strong>Mount</strong> = load the model into the Ollama daemon's memory <em>and</em> make it
          the active model for its capability. <strong>Unmount</strong> = free that memory <em>and</em>
          drop the capability back to its baseline (so features keep working). Only one model per kind
          (summary / embedding) is active at a time — mounting a new one frees the previous.</p>
        <p><strong>Loaded in memory</strong> lists what the daemon currently holds:</p>
        <ul>
          <li><span class="pin-badge">mounted</span> — pinned by you; stays until you unmount it.</li>
          <li><span class="auto-badge">auto · frees in ~Nm</span> — loaded on demand to serve a request
            (for example, embedding a <em>search query</em> — the stored document vectors don't need
            reloading, but the query itself must be embedded with the same model). Ollama frees these
            automatically after a few idle minutes; this is <em>not</em> a mount and needs no action.</li>
        </ul>
        <p><strong>Memory budget (GB)</strong>: set your machine's usable VRAM/RAM; mounting then warns
          before a load would exceed it. <strong>Compute for next mount</strong>: <em>Auto</em> lets
          Ollama decide; <em>Prefer GPU</em> offloads all layers to the GPU (if the container has GPU
          access); <em>Force CPU</em> keeps it in system RAM. After a mount, each loaded row shows
          whether it landed on <em>GPU (VRAM)</em> or <em>CPU (RAM)</em>. If you asked for GPU but it
          loaded on CPU, the Ollama container has no GPU access — on the host, install the NVIDIA
          Container Toolkit, then <code>export OLLAMA_GPU=1</code> and re-run <code>make up-ai</code>
          (this applies the <code>docker-compose.gpu.yml</code> overlay so every command keeps Ollama
          on the GPU). CPU is fine for small models but much slower for embeddings/LLMs.</p>
      </details>

      <details>
        <summary>Registered embedding models &amp; “Multimode”</summary>
        <p>A model is <em>registered</em> once you've indexed papers with it (selecting it + Save, or
          Mount — both queue a reindex that stores a dedicated vector column for that model). Because
          each registered model keeps its own vectors, you can switch between them without re-indexing.
          On the Search tab you can rank with any one registered model, or fuse them all with
          <strong>Multimode</strong> — Reciprocal-Rank-Fusion (RRF), which merges several models'
          rankings into one more-robust order. There's a cap on how many can be registered at once;
          deleting a model frees a slot.</p>
      </details>

      <details>
        <summary>Embedding index, reindex, passage search &amp; the lexical index</summary>
        <p><strong>Embedding index</strong> = how many papers have a stored vector for the active
          model. <strong>Reindex embeddings</strong> recomputes them all with the current model — run
          it after changing the embedding model, or when coverage is below 100&nbsp;%. It runs in the
          background; search keeps working on whatever is already indexed.</p>
        <p><strong>Chunk-level ANN</strong> (Approximate Nearest Neighbor) embeds individual
          passages, not just whole papers, so search can point at the most relevant <em>section</em>.
          It uses an HNSW index (a graph that finds near vectors fast) and only activates for models
          with a dedicated vector column on Postgres. Reindexing backfills these too.</p>
        <p><strong>Lexical (BM25F+) index</strong> powers classic keyword search (BM25 is the standard
          keyword-relevance ranking; the “F” weights title vs abstract vs body). It rebuilds itself
          when the library changes — <strong>Rebuild index</strong> just forces it now. This is
          separate from embeddings: keyword search needs no model and always works.</p>
      </details>

      <details>
        <summary>AI analysis &amp; “Recommend categorization” parameters</summary>
        <p>These appear on the Insights → Recommend tab, but rely on the models configured here.</p>
        <dl>
          <dt>Embedding pre-filter</dt>
          <dd>When ON, the system first uses <em>embeddings</em> (fast vector similarity) to shortlist
            the most plausible candidate tags/shelves, then asks the LLM to rank only that shortlist.
            It consumes the paper's and candidates' vectors. Use it when you have many candidate
            shelves/tags: it keeps the LLM prompt small and fast. When OFF, <em>all</em> candidates go
            to the ranker — more thorough but slower and more token-hungry, and it may exceed the
            model's context on large libraries. It needs a working embedding model; with the hash-BOW
            baseline the shortlist is coarser.</dd>
          <dt>Scoring: ranking vs affinity</dt>
          <dd><em>Ranking</em> scores by position (1st pick = K points, 2nd = K−1, …). <em>Affinity</em>
            asks the model for a 0–100 fit score per candidate; if the model doesn't return one, it
            falls back to ranking points (and flags the fallback).</dd>
          <dt>Parent combine (sum / median / max)</dt>
          <dd>How a shelf inherits relevance from its parent rack/row: sum (add), median (typical), or
            max (best parent). A shelf's final score is its own score plus a 0.5× share of its
            combined parents'.</dd>
          <dt>K, cap, recompute</dt>
          <dd><em>K</em> = how many suggestions per paper; <em>cap</em> = max papers analysed in one
            run (large scopes are truncated — you're told when); <em>recompute</em> forces a fresh run
            instead of reusing the cached one.</dd>
        </dl>
      </details>

      <details>
        <summary>Securing access (HTTPS)</summary>
        <p>Sign-in uses a bearer token sent with every request. Over <code>localhost</code> that never
          leaves your machine, but if other people reach this app over plain <strong>http://</strong>
          on your network, that token travels in the clear and could be sniffed.</p>
        <p>The production stack ships a TLS reverse proxy (Caddy). To turn on encryption for LAN users:
          copy <code>config/Caddyfile.example</code> → <code>config/Caddyfile</code> (set your server's
          LAN name), set <code>VITE_API_BASE_URL</code> to that same <code>https://</code> host in
          <code>.env</code>, run <code>make prod-up</code>, and trust Caddy's local CA on each client.
          For remote access, put it behind a VPN (WireGuard/Tailscale). Full reasoning + steps:
          <code>docs/SECURITY_TLS.md</code>.</p>
      </details>

      <details>
        <summary>Glossary</summary>
        <dl>
          <dt>Embedding / vector</dt><dd>A list of numbers representing a text's meaning; similar texts have nearby vectors.</dd>
          <dt>Cosine similarity</dt><dd>The usual measure of how “close” two vectors point — the basis of semantic ranking.</dd>
          <dt>Quantization (Q4_K_M, F16…)</dt><dd>Compressing model weights to fewer bits so they use less memory/disk, at a small quality cost. Q4 ≈ 4 bits/weight; F16 = 16-bit.</dd>
          <dt>VRAM</dt><dd>The GPU's dedicated memory. A model must fit in VRAM to run on the GPU; otherwise it runs (slower) on CPU + system RAM.</dd>
          <dt>keep_alive</dt><dd>How long Ollama keeps a model in memory after use. Mounting sets it to “forever” (pinned); a normal request uses a few minutes.</dd>
          <dt>ANN / HNSW</dt><dd>Approximate Nearest Neighbor search and the graph index that makes it fast — how passage-level semantic search scales.</dd>
          <dt>RRF (Multimode)</dt><dd>Reciprocal-Rank-Fusion: merges several rankings into one by rewarding items that rank high across models.</dd>
          <dt>TF-IDF</dt><dd>Term Frequency–Inverse Document Frequency: weights words by how distinctive they are — the topic/keyword baseline.</dd>
          <dt>BM25</dt><dd>The standard keyword-relevance ranking function used by the lexical index.</dd>
          <dt>RAKE</dt><dd>Rapid Automatic Keyword Extraction — the dependency-free keyword extractor.</dd>
          <dt>GROBID / OCR</dt><dd>GROBID extracts structured text (title, abstract, references) from a PDF; OCR adds a text layer to scanned pages first so GROBID can read them.</dd>
        </dl>
      </details>
    </div>
  </Modal>
{/if}

<style>
  h2 { font-size: 1.05rem; margin: 0 0 0.4rem; }
  h3 { font-size: 0.9rem; margin: 0; }
  .card-head { align-items: baseline; display: flex; gap: 0.75rem; justify-content: space-between; }
  .help-btn {
    background: var(--surface-sunken);
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    color: var(--ink-normal);
    cursor: pointer;
    flex: 0 0 auto;
    font-size: 0.8rem;
    font-weight: 700;
    min-height: 1.9rem;
    padding: 0.2rem 0.6rem;
  }
  .help-btn:hover { background: var(--surface-raised); }
  .help { font-size: 0.86rem; line-height: 1.5; }
  .help .lead { margin: 0 0 0.75rem; }
  .help .note { color: var(--ink-muted); font-size: 0.8rem; font-style: italic; }
  .help details {
    border-top: 1px solid var(--border-normal);
    padding: 0.5rem 0;
  }
  .help summary { cursor: pointer; font-weight: 700; padding: 0.2rem 0; }
  .help dl { margin: 0.4rem 0; }
  .help dt { font-weight: 700; margin-top: 0.5rem; }
  .help dd { color: var(--ink-normal); margin: 0.1rem 0 0; }
  .help ul { margin: 0.4rem 0; padding-left: 1.2rem; }
  .help code { font-size: 0.82em; }
  .help .pin-badge, .help .auto-badge { display: inline; }
  h3.section { margin: 1.1rem 0 0.3rem; }
  .cards {
    display: grid;
    gap: 0.75rem;
    grid-template-columns: 1fr 1fr;
    margin: 0.8rem 0;
  }
  .cap {
    background: var(--surface-sunken);
    border: 1px solid var(--border-normal);
    border-radius: 8px;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    padding: 0.75rem 0.85rem;
  }
  .cap header {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
  }
  .cap .what { color: var(--ink-normal); font-size: 0.84rem; margin: 0; }
  .cap .used { color: var(--ink-muted); font-size: 0.78rem; margin: 0; }
  .cap .reason { color: var(--ink-muted); font-size: 0.76rem; font-style: italic; margin: 0; }
  .cap label { margin-top: 0.2rem; }
  .badge {
    border-radius: 999px;
    flex: 0 0 auto;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 0.12rem 0.5rem;
    white-space: nowrap;
  }
  .badge-ok { background: var(--status-success-bg); color: var(--status-success); }
  .badge-baseline { background: var(--status-warning-bg); color: var(--status-warning); }
  .badge-off { background: var(--status-danger-bg); color: var(--status-danger); }
  .banner {
    background: var(--status-warning-bg);
    border: 1px solid var(--status-warning-border);
    border-radius: 6px;
    color: var(--status-warning);
    font-size: 0.78rem;
    margin: 0.1rem 0;
    padding: 0.4rem 0.55rem;
  }
  .row { align-items: flex-end; display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.5rem 0; }
  .row.shared { border-top: 1px solid var(--border-normal); padding-top: 0.75rem; }
  .ollama-url { flex: 1 1 16rem; }
  .vram-budget { flex: 0 0 9rem; }
  .mount-row { align-items: center; display: flex; gap: 0.5rem; margin-top: 0.1rem; }
  .mount-row button { min-height: 1.9rem; padding: 0.2rem 0.7rem; }
  .loaded-dot { color: var(--status-success); font-size: 0.75rem; font-weight: 700; white-space: nowrap; }
  .compute { flex: 0 0 15rem; }
  .pin-badge, .auto-badge {
    border-radius: 999px;
    font-size: 0.66rem;
    font-weight: 700;
    margin-left: 0.4rem;
    padding: 0.05rem 0.45rem;
    white-space: nowrap;
  }
  .pin-badge { background: var(--status-success-bg); color: var(--status-success); }
  .auto-badge { background: var(--surface-sunken); border: 1px solid var(--border-normal); color: var(--ink-muted); }
  .ollama-sema { align-items: center; color: var(--ink-muted); display: inline-flex; font-size: 0.8rem; gap: 0.35rem; white-space: nowrap; }
  .sema-dot {
    border-radius: 50%;
    box-shadow: 0 0 5px 1px var(--dot-glow, transparent);
    display: inline-block;
    height: 0.62rem;
    width: 0.62rem;
  }
  .sema-green {
    background: color-mix(in srgb, var(--status-success) 72%, white);
    --dot-glow: color-mix(in srgb, var(--status-success) 55%, transparent);
  }
  .sema-red {
    background: color-mix(in srgb, var(--status-danger) 72%, white);
    --dot-glow: color-mix(in srgb, var(--status-danger) 55%, transparent);
  }
  .hint { color: var(--status-warning); }
  .small { margin: 0.2rem 0 0.4rem; }
  .message { background: var(--status-success-bg); border-radius: 6px; padding: 0.4rem 0.6rem; }
  .models { display: flex; flex-direction: column; gap: 0.3rem; list-style: none; margin: 0.4rem 0; padding: 0; }
  .models li { align-items: center; display: flex; gap: 0.5rem; justify-content: space-between; }
  .models .small { min-height: 1.9rem; margin: 0; padding: 0.2rem 0.5rem; }
  .copy-name {
    background: none;
    border: none;
    color: var(--ink-strong);
    cursor: pointer;
    font: inherit;
    font-weight: 700;
    min-height: 0;
    padding: 0;
    text-decoration: underline dotted;
  }
  .copied { color: var(--status-success); font-size: 0.75rem; font-weight: 700; margin-left: 0.35rem; }
  .models li.unavailable { opacity: 0.7; }
  .unavail-badge {
    background: var(--status-warning-bg);
    border-radius: 999px;
    color: var(--status-warning);
    font-size: 0.68rem;
    font-weight: 700;
    margin-left: 0.4rem;
    padding: 0.05rem 0.45rem;
    white-space: nowrap;
  }
  .pull-status {
    background: var(--status-success-bg);
    border-radius: 6px;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    margin: 0.4rem 0;
    padding: 0.35rem 0.6rem;
  }
  .pull-status.failed { background: var(--status-danger-bg); }
  .pull-line { align-items: center; display: flex; gap: 0.4rem; margin: 0; }
  .pull-status progress { height: 0.55rem; width: 100%; }
  .pull-err {
    color: var(--status-danger);
    font-size: 0.8rem;
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .catalog-wrap { margin: 0.3rem 0 0.5rem; overflow-x: auto; }
  .catalog { border-collapse: collapse; font-size: 0.82rem; width: 100%; }
  .catalog th, .catalog td {
    border-bottom: 1px solid var(--border-normal);
    padding: 0.3rem 0.5rem;
    text-align: left;
    vertical-align: top;
    white-space: nowrap;
  }
  .catalog th { color: var(--ink-muted); font-size: 0.72rem; font-weight: 700; }
  .catalog .blurb { display: block; font-size: 0.72rem; white-space: normal; }
  .src-badge {
    background: var(--surface-sunken);
    border: 1px solid var(--border-normal);
    border-radius: 999px;
    color: var(--ink-muted);
    font-size: 0.66rem;
    margin-left: 0.35rem;
    padding: 0.02rem 0.4rem;
  }
  .pop {
    background: linear-gradient(to right, var(--accent-primary) var(--pop), var(--border-normal) var(--pop));
    border-radius: 999px;
    display: inline-block;
    height: 0.5rem;
    width: 4rem;
  }
  .spinner {
    animation: spin 0.8s linear infinite;
    border: 2px solid var(--border-normal);
    border-radius: 50%;
    border-top-color: var(--accent-primary);
    display: inline-block;
    height: 0.9rem;
    width: 0.9rem;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (max-width: 760px) { .cards { grid-template-columns: 1fr; } }
</style>
