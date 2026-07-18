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
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type AiConfig,
    type AiModel,
    type AiStatus,
    type CatalogModel,
    type EmbeddingModelInfo,
  } from '../api/client';
  import { errorMessage } from '../lib/ui';

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

  // Copy-to-clipboard confirmation for a clicked model name (#19).
  let copiedModel = '';

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

  // #5: poll the Jobs status for the pull job until it finishes/fails, updating a status label, a
  // byte-level progress bar (from the job's reported {done,total}) and, on failure, the real error.
  async function pollPull(jobId: string): Promise<void> {
    pullPolling = true;
    pullJobStatus = 'queued';
    pullProgress = null;
    pullError = '';
    try {
      // ~1 h ceiling at 2 s/poll — big models take a while; the loop just stops updating after that.
      for (let i = 0; i < 1800; i += 1) {
        const q = await client.getJobs(50).catch(() => null);
        const job = q?.jobs.find((j) => j.id === jobId);
        if (job) {
          pullJobStatus = job.status;
          pullProgress =
            job.progress_total && job.progress_total > 0
              ? { done: job.progress_done ?? 0, total: job.progress_total }
              : pullProgress;
          if (job.status === 'finished' || job.status === 'failed') {
            if (job.status === 'finished') {
              models = await client.listAiModels().then((r) => r.models);
              pullProgress = null;
              if (searchDone) void doSearch(); // refresh the "pulled ✓" flags in search results
            }
            if (job.status === 'failed') pullError = job.error ?? 'Pull failed (no error text).';
            return;
          }
        }
        await new Promise((r) => window.setTimeout(r, 2000));
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
  <h2>AI &amp; Models</h2>
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
      <span class="muted">Ollama: {status.ollama_reachable ? 'reachable ✓' : 'not reachable'}</span>
    </div>

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
              <small class="muted">{m.provider} {fmtSize(m.size_bytes)}</small>
            </span>
            <button type="button" class="secondary small" on:click={() => remove(m)} disabled={busy} title="Delete this downloaded model">Delete</button>
          </li>
        {/each}
      </ul>
    {/if}

    <!-- #21: registered embedding models + the model cap. -->
    <h3 class="section">Registered embedding models</h3>
    <p class="muted small">
      Models registered for semantic search{maxModels ? ` (cap: ${maxModels})` : ''}. The Search tab
      can rank with any of these, or fuse all of them (“Multimode”).
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

    <h3 class="section">Embedding index</h3>
    <p class="muted small">
      How many papers currently have an embedding for the active model. Reindexing rebuilds them all
      — do this after changing the embedding provider/model so semantic search stays consistent.
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

<style>
  h2 { font-size: 1.05rem; margin: 0 0 0.4rem; }
  h3 { font-size: 0.9rem; margin: 0; }
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
