<script lang="ts">
  import { onMount } from 'svelte';

  import {
    ApiClient,
    type BatchCommitDraft,
    type EngineKind,
    type ImportBatch,
    type ParsedDraft,
  } from '../api/client';
  import { pendingImportText } from '../lib/selection';
  import { errorMessage } from '../lib/ui';
  import ShelfPicker from './ShelfPicker.svelte';

  export let client: ApiClient;

  type Row = {
    draft: ParsedDraft;
    include: boolean;
    title: string;
    authors: string;
    year: string;
    doi: string;
    venue: string;
    // Index into draft.candidates for the chosen candidate, or -1 for "edited by hand".
    candidateIndex: number;
  };

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
  let rows: Row[] = [];
  let degraded = false;
  let grobidUnavailable = false;
  let targetShelfId = '';
  let enrich = true;
  let loading = false;
  let message = '';
  let lastBatch: ImportBatch | null = null;

  // Non-empty, trimmed input lines. Declared reactively (not as a plain function) so the
  // `disabled` binding below re-evaluates when `text` changes — a template expression only
  // tracks variables it references directly, never ones read inside a called function.
  $: lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);

  function rowFromDraft(draft: ParsedDraft): Row {
    return {
      draft,
      // Default-checked for confident matches; reviewers opt in to the rest.
      include: draft.match_status === 'matched',
      title: draft.suggested_title ?? draft.raw_line,
      authors: draft.suggested_authors.join('; '),
      year: draft.suggested_year != null ? String(draft.suggested_year) : '',
      doi: draft.suggested_doi ?? '',
      venue: draft.suggested_venue ?? '',
      candidateIndex: -1,
    };
  }

  function applyCandidate(row: Row): void {
    const c = row.draft.candidates[row.candidateIndex];
    if (!c) return;
    row.title = c.title ?? row.title;
    row.authors = c.authors.join('; ');
    row.year = c.year != null ? String(c.year) : '';
    row.doi = c.doi ?? '';
    row.venue = c.venue ?? '';
    rows = rows; // trigger reactivity
  }

  async function preview(): Promise<void> {
    if (!lines.length) return;
    loading = true;
    message = '';
    lastBatch = null;
    try {
      const result = await client.batchImportPreview(lines, engine);
      rows = result.drafts.map(rowFromDraft);
      degraded = result.degraded;
      grobidUnavailable = result.grobid_unavailable;
    } catch (error) {
      message = errorMessage(error);
    } finally {
      loading = false;
    }
  }

  async function commit(): Promise<void> {
    const selected = rows.filter((r) => r.include);
    if (!selected.length) {
      message = 'Select at least one paper to commit.';
      return;
    }
    const drafts: BatchCommitDraft[] = selected.map((r) => ({
      title: r.title.trim() || null,
      authors: r.authors
        .split(';')
        .map((a) => a.trim())
        .filter(Boolean),
      year: r.year.trim() ? Number(r.year.trim()) : null,
      doi: r.doi.trim() || null,
      venue: r.venue.trim() || null,
      abstract: null,
      include: true,
    }));
    loading = true;
    message = '';
    try {
      lastBatch = await client.batchImportCommit(drafts, {
        engine,
        targetShelfId: targetShelfId || null,
        enrich,
      });
      const s = lastBatch.stats ?? {};
      message = `Committed: ${s.created ?? 0} created, ${s.matched ?? 0} matched, ${s.skipped ?? 0} skipped${
        s.added_to_shelf ? `, ${s.added_to_shelf} added to shelf` : ''
      }.`;
      rows = [];
      text = '';
    } catch (error) {
      message = errorMessage(error);
    } finally {
      loading = false;
    }
  }

  function badge(status: string): string {
    if (status === 'matched') return 'matched';
    if (status === 'title_only') return 'title only';
    return 'no match';
  }
</script>

<div class="batch">
  <h2>Batch import citations</h2>
  <p class="muted">
    Paste raw citations or titles, one per line. <strong>Lookup</strong> searches Crossref /
    OpenAlex / Semantic Scholar; <strong>GROBID</strong> parses the reference strings. Review the
    suggestions, then commit the papers you want.
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
    <button type="button" on:click={preview} disabled={loading || !lines.length}>
      Preview
    </button>
  </div>

  {#if message}<p class="msg">{message}</p>{/if}
  {#if degraded}
    <p class="banner warn">Some lines were skipped for the time budget and left as title-only.</p>
  {/if}
  {#if grobidUnavailable}
    <p class="banner warn">
      GROBID is unavailable — every line fell back to title-only. Start the extraction service or
      use the Lookup engine.
    </p>
  {/if}

  {#if rows.length}
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Include</th>
            <th>Confidence</th>
            <th>Title</th>
            <th>Authors</th>
            <th>Year</th>
            <th>DOI</th>
            <th>Venue</th>
            <th>Candidate</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as row (row.draft.line_index)}
            <tr>
              <td><input type="checkbox" bind:checked={row.include} aria-label="Include this paper" /></td>
              <td><span class="pill {row.draft.match_status}">{badge(row.draft.match_status)}</span></td>
              <td><input bind:value={row.title} aria-label="Title" /></td>
              <td><input bind:value={row.authors} aria-label="Authors (semicolon-separated)" /></td>
              <td><input class="yr" bind:value={row.year} aria-label="Year" /></td>
              <td><input bind:value={row.doi} aria-label="DOI" /></td>
              <td><input bind:value={row.venue} aria-label="Venue" /></td>
              <td>
                {#if row.draft.candidates.length > 1}
                  <select
                    bind:value={row.candidateIndex}
                    on:change={() => applyCandidate(row)}
                    aria-label="Choose a candidate"
                  >
                    <option value={-1}>Custom</option>
                    {#each row.draft.candidates as cand, i (i)}
                      <option value={i}>
                        {cand.title ?? '—'} ({Math.round(cand.confidence * 100)}%)
                      </option>
                    {/each}
                  </select>
                {:else}
                  <span class="muted">—</span>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <div class="commit-row">
      <ShelfPicker {client} bind:value={targetShelfId} label="Add committed papers to shelf" />
      <label class="enrich">
        <input type="checkbox" bind:checked={enrich} /> Enrich new papers (DOI metadata)
      </label>
      <button type="button" on:click={commit} disabled={loading}>Commit selected</button>
    </div>
  {/if}
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

  .table-scroll {
    overflow-x: auto;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }

  th,
  td {
    border-bottom: 1px solid var(--border-normal);
    padding: 0.3rem 0.4rem;
    text-align: left;
  }

  td input {
    width: 100%;
    min-width: 8rem;
  }

  td input.yr {
    min-width: 4rem;
  }

  .pill {
    border-radius: 999px;
    padding: 0.1rem 0.5rem;
    font-size: 0.75rem;
    white-space: nowrap;
    background: var(--surface-sunken);
  }

  .pill.matched {
    background: var(--status-success-bg);
    color: var(--status-success);
  }

  .pill.title_only {
    background: var(--status-warning-bg);
    color: var(--status-warning);
  }

  .pill.no_match {
    background: var(--status-danger-bg);
    color: var(--status-danger);
  }

  .commit-row {
    display: flex;
    gap: 1rem;
    align-items: flex-end;
    flex-wrap: wrap;
  }

  .enrich {
    display: flex;
    gap: 0.3rem;
    align-items: center;
    font-size: 0.85rem;
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
