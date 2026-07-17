<!-- DraftReview — review/edit table for parsed import drafts (batch citation import) before they
     are committed into the library. Props: client (ApiClient), gradual (parent is still streaming
     drafts in via addDrafts(), e.g. chunked search — committed rows are removed but the table stays
     open for more). Exposes imperative methods addDrafts()/reset() for the parent to push rows in
     and clear the table.
     Events/callbacks: dispatches `committed` with { remaining, batch } after a successful commit.
     Non-obvious: `include` defaults to checked only for confident, not-already-in-library matches;
     picking a candidate from the dropdown overwrites the row's editable fields (applyCandidate). -->
<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  import {
    ApiClient,
    type BatchCommitDraft,
    type ImportBatch,
    type ParsedDraft,
  } from '../api/client';
  import { errorMessage } from '../lib/ui';
  import ShelfPicker from './ShelfPicker.svelte';
  import { ensureShelves, shelves } from '../lib/catalog';

  export let client: ApiClient;
  // While the parent is still producing drafts (chunked search), a commit is partial: committed
  // rows leave the table, everything else stays reviewable and the search keeps appending.
  export let gradual = false;

  const dispatch = createEventDispatcher<{
    committed: { remaining: number; batch: ImportBatch };
  }>();

  type Row = {
    draft: ParsedDraft;
    include: boolean;
    // Optional per-row shelf override ('' = the global "Add committed papers to shelf" pick).
    shelfId: string;
    title: string;
    authors: string;
    year: string;
    doi: string;
    venue: string;
    // Index into draft.candidates for the chosen candidate, or -1 for "edited by hand".
    candidateIndex: number;
  };

  let rows: Row[] = [];
  let targetShelfId = '';
  let enrich = true;
  let loading = false;
  let message = '';

  export function addDrafts(drafts: ParsedDraft[]): void {
    rows = [...rows, ...drafts.map(rowFromDraft)];
  }

  export function reset(): void {
    rows = [];
    message = '';
  }

  function rowFromDraft(draft: ParsedDraft): Row {
    return {
      draft,
      // Default-checked for confident matches; reviewers opt in to the rest. Already-in-library
      // entries start unchecked — committing them only re-matches (and shelves) the existing paper.
      include: draft.match_status === 'matched' && !draft.existing_work_id,
      shelfId: '',
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

  async function commit(): Promise<void> {
    const selected = rows.filter((r) => r.include);
    if (!selected.length) {
      message = 'Select at least one paper to commit.';
      return;
    }
    // All drafts in one review share the engine that produced them.
    const engine = selected[0].draft.engine;
    const drafts: BatchCommitDraft[] = selected.map((r) => ({
      title: r.title.trim() || null,
      authors: r.authors
        .split(';')
        .map((a) => a.trim())
        .filter(Boolean),
      year: r.year.trim() ? Number(r.year.trim()) : null,
      doi: r.doi.trim() || null,
      venue: r.venue.trim() || null,
      abstract: r.draft.suggested_abstract ?? null,
      include: true,
      arxiv_id: r.draft.suggested_arxiv_id ?? null,
      work_type: r.draft.suggested_work_type ?? null,
      // Per-row shelf override; the server falls back to the commit's global target shelf.
      target_shelf_id: r.shelfId || null,
    }));
    loading = true;
    message = '';
    try {
      const batch = await client.batchImportCommit(drafts, {
        engine,
        targetShelfId: targetShelfId || null,
        enrich,
      });
      const s = batch.stats ?? {};
      message = `Committed: ${s.created ?? 0} created, ${s.matched ?? 0} matched, ${s.skipped ?? 0} skipped${
        s.added_to_shelf ? `, ${s.added_to_shelf} added to shelf` : ''
      }.`;
      const committed = new Set(selected.map((r) => r.draft.line_index));
      rows = rows.filter((r) => !committed.has(r.draft.line_index));
      dispatch('committed', { remaining: rows.length, batch });
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

  function badgeTitle(status: string): string {
    if (status === 'matched') {
      return 'A confident candidate was found and prefilled these fields. Committing creates a new paper — unless one with the same DOI/title is already in the library, in which case it is matched instead of duplicated (and just added to the chosen shelf).';
    }
    if (status === 'title_only') {
      return 'No confident candidate was found — the pasted line was kept as the title. Edit the fields or commit as-is to create a minimal paper.';
    }
    return 'The lookup found no candidate. You can still fill in the fields by hand and commit to create the paper.';
  }
</script>

{#if message}<p class="msg">{message}</p>{/if}

{#if rows.length}
  <!-- Two rows per draft (UX batch): the title gets the full first row so it stays readable;
       the shorter fields share the second row. -->
  <div class="drafts">
    {#each rows as row (row.draft.line_index)}
      <div class="draft">
        <div class="draft-title-row">
          <input type="checkbox" bind:checked={row.include} aria-label="Include this paper" />
          <span class="pill {row.draft.match_status}" title={badgeTitle(row.draft.match_status)}
            >{badge(row.draft.match_status)}</span>
          {#if row.draft.existing_work_id}
            <span class="pill in-library"
              title="Already in your library — committing it only adds the existing paper to the chosen shelf"
              >in library</span>
          {/if}
          <input class="title-input" bind:value={row.title} aria-label="Title" placeholder="Title" />
        </div>
        <div class="draft-fields">
          <label class="field">
            <span class="field-label">Authors</span>
            <input bind:value={row.authors} aria-label="Authors (semicolon-separated)" />
          </label>
          <label class="field yr-field">
            <span class="field-label">Year</span>
            <input class="yr" bind:value={row.year} aria-label="Year" />
          </label>
          <label class="field">
            <span class="field-label">DOI</span>
            <input bind:value={row.doi} aria-label="DOI" />
          </label>
          <label class="field">
            <span class="field-label">Venue</span>
            <input bind:value={row.venue} aria-label="Venue" />
          </label>
          <label class="field">
            <span class="field-label">Shelf</span>
            <select bind:value={row.shelfId} aria-label="Shelf for this paper"
              title="Shelf for this paper — overrides the global pick below">
              <option value="">(use pick below)</option>
              {#each $shelves as shelf (shelf.id)}
                <option value={shelf.id}>{shelf.name}</option>
              {/each}
            </select>
          </label>
          {#if row.draft.candidates.length > 1}
            <label class="field">
              <span class="field-label">Candidate</span>
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
            </label>
          {/if}
        </div>
      </div>
    {/each}
  </div>

  <div class="commit-row">
    <ShelfPicker {client} bind:value={targetShelfId} label="Add committed papers to shelf" />
    <label class="enrich">
      <input type="checkbox" bind:checked={enrich} /> Enrich new papers (DOI metadata)
    </label>
    <button type="button" on:click={commit} disabled={loading}
      title={gradual
        ? 'Import the checked entries found so far; the search keeps running'
        : 'Import the checked entries'}
      >{gradual ? 'Commit selected now' : 'Commit selected'}</button>
  </div>
{/if}

<style>
  .drafts {
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    overflow: hidden;
  }

  .draft {
    padding: 0.45rem 0.6rem 0.55rem;
  }

  .draft + .draft {
    border-top: 1px solid var(--border-normal);
  }

  .draft-title-row {
    align-items: center;
    display: flex;
    gap: 0.5rem;
  }

  .title-input {
    flex: 1;
    font-weight: 600;
    min-width: 0;
  }

  .draft-fields {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(10rem, 2fr) 4.5rem minmax(8rem, 1.5fr) minmax(8rem, 1.5fr) minmax(10rem, 1.5fr);
    margin-top: 0.35rem;
  }

  @media (max-width: 900px) {
    .draft-fields {
      grid-template-columns: 1fr 1fr;
    }
  }

  .field {
    display: grid;
    gap: 0.1rem;
    min-width: 0;
  }

  .field input,
  .field select {
    min-width: 0;
    width: 100%;
  }

  .field-label {
    color: var(--ink-muted);
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
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

  .pill.in-library {
    background: var(--status-warning-bg);
    color: var(--status-warning);
  }

  .commit-row {
    display: flex;
    gap: 1rem;
    align-items: flex-end;
    flex-wrap: wrap;
    margin-top: 0.6rem;
  }

  .enrich {
    display: flex;
    gap: 0.3rem;
    align-items: center;
    font-size: 0.85rem;
  }

  .msg {
    margin: 0;
    font-size: 0.85rem;
  }
</style>
