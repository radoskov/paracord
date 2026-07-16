<!-- BatchImport — paste-many-citations workflow: chunked lookup/GROBID preview, then review/commit.
     Props: client (ApiClient).
     Events/callbacks: none exported — wraps DraftReview, which emits `committed`.
     Non-obvious lifecycle/state: subscribes to the `pendingImportText` store so an external
     "push to import" action (reference-graph node) can prefill the textarea; lines are looked
     up in small chunks (LOOKUP_CHUNK) so results stream in, a failed/timed-out chunk degrades to
     title-only rows instead of failing the whole batch, and the search can be cancelled between
     chunks while already-found rows stay committable via DraftReview's `gradual` mode. -->
<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiClient, type EngineKind, type ParsedDraft } from '../api/client';
  import { pendingImportText } from '../lib/selection';
  import DraftReview from './DraftReview.svelte';

  export let client: ApiClient;

  let text = '';
  let engine: EngineKind = 'lookup';

  // 5g: a reference-graph external node can prefill this box. Append the pushed citation line (on a
  // fresh line) and clear the store so it isn't re-applied.
  onMount(() =>
    pendingImportText.subscribe((val) => {
      if (!val) return;
      text = text.trim() ? `${text.replace(/\s*$/, '')}\n${val}` : val;
      pendingImportText.set(null);
    }),
  );

  let review: DraftReview;
  let message = '';
  let degraded = false;
  let grobidUnavailable = false;

  // Chunked lookup (UX batch): lines are looked up a few at a time so the preview fills in
  // progressively, a failed/timed-out chunk degrades to title-only rows instead of sinking the
  // whole batch, and the search can be cancelled between chunks. Already-found rows can be
  // committed while the search keeps running (gradual import — see DraftReview).
  const LOOKUP_CHUNK = 4;
  let searching = false;
  let cancelRequested = false;
  let progressDone = 0;
  let progressTotal = 0;
  let failedLines = 0;

  // Non-empty, trimmed input lines. Declared reactively (not as a plain function) so the
  // `disabled` binding below re-evaluates when `text` changes — a template expression only
  // tracks variables it references directly, never ones read inside a called function.
  $: lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);

  function fallbackDraft(line: string, index: number): ParsedDraft {
    return {
      line_index: index,
      raw_line: line,
      engine,
      suggested_title: line,
      suggested_authors: [],
      suggested_year: null,
      suggested_doi: null,
      suggested_venue: null,
      suggested_abstract: null,
      match_status: 'title_only',
      candidates: [],
    };
  }

  async function preview(): Promise<void> {
    if (!lines.length || searching) return;
    const all = lines;
    searching = true;
    cancelRequested = false;
    message = '';
    review.reset();
    degraded = false;
    grobidUnavailable = false;
    failedLines = 0;
    progressDone = 0;
    progressTotal = all.length;
    try {
      for (let offset = 0; offset < all.length; offset += LOOKUP_CHUNK) {
        if (cancelRequested) {
          message = `Search cancelled — ${all.length - offset} line(s) were not looked up.`;
          break;
        }
        const chunk = all.slice(offset, offset + LOOKUP_CHUNK);
        try {
          const result = await client.batchImportPreview(chunk, engine);
          // Remap the per-request indices to global ones so row keys stay unique across chunks.
          review.addDrafts(
            result.drafts.map((d) => ({ ...d, line_index: offset + d.line_index })),
          );
          degraded = degraded || result.degraded;
          grobidUnavailable = grobidUnavailable || result.grobid_unavailable;
        } catch {
          // One failed chunk must not sink the batch — keep its lines editable as title-only.
          failedLines += chunk.length;
          review.addDrafts(chunk.map((line, i) => fallbackDraft(line, offset + i)));
        }
        progressDone = Math.min(offset + chunk.length, all.length);
      }
    } finally {
      searching = false;
    }
  }

  function onCommitted(event: CustomEvent<{ remaining: number }>): void {
    // Once everything the user wanted is committed and the search is idle, clear the input.
    if (!searching && event.detail.remaining === 0) text = '';
  }
</script>

<div class="batch">
  <h2>Batch import citations</h2>
  <p class="muted">
    Paste raw citations or titles, one per line. <strong>Lookup</strong> searches Crossref /
    OpenAlex / Semantic Scholar; <strong>GROBID</strong> parses the reference strings. Review the
    suggestions, then commit the papers you want — you can already commit while the search keeps
    running.
  </p>

  <textarea
    bind:value={text}
    rows="5"
    placeholder={'Smith et al. Attention is all you need. NeurIPS 2017\nAnother citation…'}
    aria-label="Citations, one per line"
  ></textarea>

  <div class="controls">
    <fieldset>
      <legend class="sr-only">Engine</legend>
      <label><input type="radio" bind:group={engine} value="lookup" /> Lookup</label>
      <label><input type="radio" bind:group={engine} value="grobid" /> GROBID</label>
    </fieldset>
    <button type="button" on:click={preview} disabled={searching || !lines.length}>
      Preview
    </button>
  </div>

  {#if searching}
    <div class="progress-row">
      <progress value={progressDone} max={progressTotal}></progress>
      <span class="progress-text">Looked up {progressDone} of {progressTotal} line(s)…</span>
      <button type="button" class="secondary" on:click={() => (cancelRequested = true)}
        disabled={cancelRequested}
        title="Stop after the current chunk; already-found entries stay reviewable"
        >{cancelRequested ? 'Cancelling…' : 'Cancel search'}</button>
    </div>
  {/if}

  {#if message}<p class="msg">{message}</p>{/if}
  {#if failedLines}
    <p class="banner warn">
      {failedLines} line(s) failed to look up (kept as title-only — edit or commit them anyway).
    </p>
  {/if}
  {#if degraded}
    <p class="banner warn">Some lines were skipped for the time budget and left as title-only.</p>
  {/if}
  {#if grobidUnavailable}
    <p class="banner warn">
      GROBID is unavailable — every line fell back to title-only. Start the extraction service or
      use the Lookup engine.
    </p>
  {/if}

  <DraftReview bind:this={review} {client} gradual={searching} on:committed={onCommitted} />
</div>

<style>
  .batch {
    display: grid;
    gap: 0.6rem;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0;
  }

  textarea {
    resize: vertical;
    width: 100%;
  }

  .controls {
    display: flex;
    gap: 1rem;
    align-items: center;
  }

  fieldset {
    border: none;
    padding: 0;
    margin: 0;
    display: flex;
    gap: 1rem;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
  }

  .progress-row {
    align-items: center;
    display: flex;
    gap: 0.6rem;
  }

  .progress-row progress {
    flex: 1;
    min-width: 0;
  }

  .progress-text {
    color: var(--ink-muted);
    font-size: 0.85rem;
    white-space: nowrap;
  }

  .banner {
    margin: 0;
    padding: 0.4rem 0.6rem;
    border-radius: 0.3rem;
    font-size: 0.85rem;
  }

  .warn {
    background: var(--status-warning-bg);
    color: var(--status-warning);
  }

  .msg {
    margin: 0;
    font-size: 0.85rem;
  }
</style>
