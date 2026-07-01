<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type AiConfig,
    type AiModel,
    type AiStatus,
  } from '../api/client';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let config: AiConfig | null = null;
  let status: AiStatus | null = null;
  let models: AiModel[] = [];

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
      const [st, mdl] = await Promise.all([client.getAiStatus(), client.listAiModels()]);
      status = st;
      config = st.config;
      models = mdl.models;
    });
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
      return { kind: 'baseline', label: 'Built-in baseline', reason: 'Dependency-free extractive summariser — always works.' };
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
    if (config.ocr_backend === 'ocrmypdf') {
      const ok = avail('extraction', 'ocrmypdf');
      return ok
        ? { kind: 'ok', label: 'Available', reason: 'OCRmyPDF adds a text layer to scanned/poor-text PDFs before GROBID.' }
        : { kind: 'off', label: 'OCR unavailable', reason: note('extraction', 'ocrmypdf') };
    }
    // full_ml
    const ok = avail('extraction', 'full_ml');
    return ok
      ? { kind: 'ok', label: 'Available', reason: null }
      : { kind: 'off', label: 'Degrades to GROBID', reason: note('extraction', 'full_ml') };
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

  // True when full_ml OCR is selected but no ML extractor is installed — show install guidance
  // (never a runtime install button; the opt-in image is built with `make build-ml-extraction`).
  $: ocrMlUnavailable =
    config != null && config.ocr_backend === 'full_ml' && !avail('extraction', 'full_ml');

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
        <label>Embedding model
          <input bind:value={config.embedding_model} placeholder="(provider default)" disabled={busy}
            title="Specific embedding model name, or blank for the provider default" />
        </label>
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
        <p class="used">Used for: the "Summarise" action on a paper and the scope summaries in Insights.</p>
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
        <label>Summary model (Ollama)
          <input bind:value={config.summary_model} disabled={busy}
            title="Ollama model used for summaries when the provider is local_llm" />
        </label>
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
        {#if ocrMlUnavailable}
          <p class="banner" title="The full-ML extractors (Nougat/Marker) are an opt-in image build, not a runtime install">
            No ML extractor is installed. Build the opt-in ML-extraction image
            (<code>make build-ml-extraction</code>) to enable it; until then this degrades to GROBID.
          </p>
        {/if}
        <label>Extraction backend
          <select bind:value={config.ocr_backend} disabled={busy}
            title="How PDF text is extracted before GROBID (OCRmyPDF adds a text layer; full_ml is an opt-in ML extractor)">
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
    {#if models.length === 0}
      <p class="empty">No local models. Pull one above (needs the Ollama profile running).</p>
    {:else}
      <ul class="models">
        {#each models as m (m.provider + m.name)}
          <li>
            <span>{m.name} <small class="muted">{m.provider} {fmtSize(m.size_bytes)}</small></span>
            <button type="button" class="secondary small" on:click={() => remove(m)} disabled={busy} title="Delete this downloaded model">Delete</button>
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
          : 'not yet warmed (builds on first search / library open)'}.
      </p>
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
    background: #f8fafc;
    border: 1px solid #e2e8f0;
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
  .cap .what { color: #203142; font-size: 0.84rem; margin: 0; }
  .cap .used { color: #64717f; font-size: 0.78rem; margin: 0; }
  .cap .reason { color: #526070; font-size: 0.76rem; font-style: italic; margin: 0; }
  .cap label { margin-top: 0.2rem; }
  .badge {
    border-radius: 999px;
    flex: 0 0 auto;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 0.12rem 0.5rem;
    white-space: nowrap;
  }
  .badge-ok { background: #bbf7d0; color: #14532d; }
  .badge-baseline { background: #fef3c7; color: #78350f; }
  .badge-off { background: #fecaca; color: #7f1d1d; }
  .banner {
    background: #fff7ed;
    border: 1px solid #fed7aa;
    border-radius: 6px;
    color: #9a3412;
    font-size: 0.78rem;
    margin: 0.1rem 0;
    padding: 0.4rem 0.55rem;
  }
  .row { align-items: flex-end; display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.5rem 0; }
  .row.shared { border-top: 1px solid #e2e8f0; padding-top: 0.75rem; }
  .ollama-url { flex: 1 1 16rem; }
  .hint { color: #8a6d3b; }
  .small { margin: 0.2rem 0 0.4rem; }
  .message { background: #eef4ef; border-radius: 6px; padding: 0.4rem 0.6rem; }
  .models { display: flex; flex-direction: column; gap: 0.3rem; list-style: none; margin: 0.4rem 0; padding: 0; }
  .models li { align-items: center; display: flex; gap: 0.5rem; justify-content: space-between; }
  .models .small { min-height: 1.9rem; margin: 0; padding: 0.2rem 0.5rem; }
  @media (max-width: 760px) { .cards { grid-template-columns: 1fr; } }
</style>
