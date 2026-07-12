<script lang="ts">
  import type { ReadingStatus, Work, WorkSortKey } from '../api/client';
  import { type ColumnDef, type ColumnId, LIBRARY_COLUMNS } from '../lib/columns';
  import { canModifyWork, currentUser, INSUFFICIENT_ROLE } from '../lib/session';
  import { formatDate } from '../lib/ui';

  export let works: Work[] = [];
  export let selectedWorkId: string | null = null;
  export let compact = false;
  // The row for this work id briefly pulses (the "jump to open paper" affordance, L3). Rows carry a
  // `data-work-id` so callers can scroll a specific row into view.
  export let flashWorkId: string | null = null;

  // Ordered, visible columns to render. Defaults to the registry's default-visible set so callers
  // that don't pass `columns` get the standard library table.
  export let columns: ColumnDef[] = LIBRARY_COLUMNS.filter((c) => c.default);

  // Optional sorting (clickable headers). Off by default so other callers are unaffected.
  export let sortable = false;
  export let sortKey: WorkSortKey | null = null;
  export let sortOrder: 'asc' | 'desc' = 'desc';
  export let onSort: (key: WorkSortKey) => void = () => {};

  // Optional multi-select (checkbox column). Off by default so other callers are unaffected.
  export let selectable = false;
  export let selectedIds: string[] = [];
  export let allSelected = false;
  export let onToggleSelect: (id: string) => void = () => {};
  export let onToggleAll: (checked: boolean) => void = () => {};

  export let onSelect: (work: Work) => void | Promise<void> = () => {};
  export let onStatusChange: (work: Work, status: ReadingStatus) => void = () => {};

  // In compact mode hide the lower-priority Venue column (keeps the old compact behaviour without
  // the nth-child CSS hack).
  $: shownColumns = compact ? columns.filter((c) => c.id !== 'venue') : columns;
  $: colCount = shownColumns.length + (selectable ? 1 : 0);

  const readingStatuses: ReadingStatus[] = [
    'unread',
    'skimmed',
    'reading',
    'read',
    'important',
    'revisit',
  ];

  function titleFor(work: Work): string {
    return work.canonical_title?.trim() || 'Untitled paper';
  }

  // Map a backend badge token to a human label + a state class (drives the chip colour). Unknown
  // tokens fall back to the raw token with a neutral style so a new backend token never breaks.
  const BADGE_META: Record<string, { label: string; state: string }> = {
    extracted: { label: 'extracted', state: 'ok' },
    extract_failed: { label: 'extraction failed', state: 'error' },
    not_extracted: { label: 'not extracted', state: 'muted' },
    text_poor: { label: 'poor text', state: 'warn' },
    text_none: { label: 'no text layer', state: 'warn' },
    ocr_added: { label: 'OCR', state: 'info' },
    conflicts: { label: 'conflicts', state: 'warn' },
  };
  function badgeMeta(token: string): { label: string; state: string } {
    return BADGE_META[token] ?? { label: token.replaceAll('_', ' '), state: 'muted' };
  }

  function isActive(col: ColumnDef): boolean {
    return sortable && !!col.sortKey && col.sortKey === sortKey;
  }
</script>

<table class:compact>
  <thead>
    <tr>
      {#if selectable}
        <th class="check">
          <input
            type="checkbox"
            checked={allSelected}
            on:change={(e) => onToggleAll(e.currentTarget.checked)}
            title="Select all shown"
            aria-label="Select all"
          />
        </th>
      {/if}
      {#each shownColumns as col (col.id)}
        <th>
          {#if sortable && col.sortKey}
            <button
              type="button"
              class="sort"
              class:active={isActive(col)}
              on:click={() => onSort(col.sortKey as WorkSortKey)}
              title="Sort by {col.label.toLowerCase()}"
            >
              {col.label}
              {#if isActive(col)}<span class="indicator">{sortOrder === 'asc' ? '▲' : '▼'}</span>{/if}
            </button>
          {:else}
            {col.label}
          {/if}
        </th>
      {/each}
    </tr>
  </thead>
  <tbody>
    {#if works.length === 0}
      <tr>
        <td colspan={colCount} class="empty">No papers</td>
      </tr>
    {:else}
      {#each works as work (work.id)}
        <tr
          data-work-id={work.id}
          class:selected={work.id === selectedWorkId}
          class:flash={work.id === flashWorkId}
          on:click={() => onSelect(work)}
          tabindex="0"
          on:keydown={(event) => event.key === 'Enter' && onSelect(work)}
        >
          {#if selectable}
            <td class="check" on:click|stopPropagation>
              <input
                type="checkbox"
                checked={selectedIds.includes(work.id)}
                on:change={() => onToggleSelect(work.id)}
                title="Select this paper for batch actions"
                aria-label="Select paper"
              />
            </td>
          {/if}
          {#each shownColumns as col (col.id)}
            {#if col.id === 'title'}
              <td>
                <strong>{titleFor(work)}</strong>
                {#if work.arxiv_id}
                  <span>{work.arxiv_id}</span>
                {/if}
              </td>
            {:else if col.id === 'year'}
              <td>{work.year ?? '-'}</td>
            {:else if col.id === 'venue'}
              <td>{work.venue ?? '-'}</td>
            {:else if col.id === 'status'}
              {@const canModify = canModifyWork($currentUser, work)}
              <td on:click|stopPropagation>
                <select
                  value={work.reading_status}
                  disabled={!canModify}
                  title={canModify ? 'Change this paper’s reading status' : INSUFFICIENT_ROLE}
                  on:change={(event) =>
                    onStatusChange(work, event.currentTarget.value as ReadingStatus)}
                >
                  {#each readingStatuses as status}
                    <option value={status}>{status}</option>
                  {/each}
                </select>
              </td>
            {:else if col.id === 'added_at'}
              <td>{formatDate(work.created_at)}</td>
            {:else if col.id === 'doi'}
              <td>{work.doi ?? '-'}</td>
            {:else if col.id === 'arxiv_id'}
              <td>{work.arxiv_id ?? '-'}</td>
            {:else if col.id === 'reference_count'}
              <td class="num">{work.reference_count ?? 0}</td>
            {:else if col.id === 'citation_count'}
              <td class="num">{work.citation_count ?? '-'}</td>
            {:else if col.id === 'local_reference_count'}
              <td class="num">{work.local_reference_count ?? 0}</td>
            {:else if col.id === 'local_citation_count'}
              <td class="num">{work.local_citation_count ?? 0}</td>
            {:else if col.id === 'keywords'}
              <td>
                {#if work.keywords?.length}
                  <span class="keywords">
                    {#each work.keywords.slice(0, 5) as kw}<span class="kw">{kw}</span>{/each}
                  </span>
                {:else}-{/if}
              </td>
            {:else if col.id === 'shelves'}
              <td>{work.shelves?.length ? work.shelves.map((s) => s.name).join(', ') : '-'}</td>
            {:else if col.id === 'racks'}
              <td>{work.racks?.length ? work.racks.map((r) => r.name).join(', ') : '-'}</td>
            {:else if col.id === 'file_count'}
              <td class="num">{work.file_count ?? 0}</td>
            {:else if col.id === 'topics'}
              <td>
                {#if work.topics?.length}
                  <span class="keywords">
                    {#each work.topics.slice(0, 5) as t}<span class="kw">{t}</span>{/each}
                  </span>
                {:else}-{/if}
              </td>
            {:else if col.id === 'badges'}
              <td>
                {#if work.badges?.length}
                  <span class="badges">
                    {#each work.badges as token}
                      {@const meta = badgeMeta(token)}
                      <span class="badge badge-{meta.state}">{meta.label}</span>
                    {/each}
                  </span>
                {:else}-{/if}
              </td>
            {:else if col.id === 'tags'}
              <td>
                {#if work.tags?.length}
                  <span class="keywords">
                    {#each work.tags as tag}
                      <span class="kw tag-kw" style={tag.color ? `--tag-color:${tag.color}` : ''}
                      >{tag.name}</span>
                    {/each}
                  </span>
                {:else}-{/if}
              </td>
            {/if}
          {/each}
        </tr>
      {/each}
    {/if}
  </tbody>
</table>

<style>
  table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
    font-size: 0.92rem;
  }

  th,
  td {
    border-bottom: 1px solid var(--border-normal);
    padding: 0.66rem 0.7rem;
    text-align: left;
    vertical-align: middle;
  }

  th {
    color: var(--ink-muted);
    font-size: 0.76rem;
    font-weight: 700;
    text-transform: uppercase;
  }

  .sort {
    background: none;
    border: none;
    color: inherit;
    cursor: pointer;
    font: inherit;
    letter-spacing: inherit;
    min-height: auto;
    padding: 0;
    text-transform: inherit;
  }

  .sort.active {
    color: var(--accent-primary);
  }

  .indicator {
    font-size: 0.7rem;
    margin-left: 0.15rem;
  }

  tr {
    cursor: pointer;
  }

  tbody tr:hover,
  tbody tr.selected {
    background: var(--status-success-bg);
  }

  tbody tr.flash {
    animation: row-flash 1.5s ease-out;
  }

  @keyframes row-flash {
    0%,
    40% {
      background: var(--accent-primary);
      color: var(--ink-inverse);
    }
    100% {
      background: transparent;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    tbody tr.flash {
      animation: none;
      outline: 2px solid var(--accent-primary);
      outline-offset: -2px;
    }
  }

  strong,
  span {
    display: block;
    overflow-wrap: anywhere;
  }

  span {
    color: var(--ink-muted);
    font-size: 0.78rem;
    margin-top: 0.18rem;
  }

  select {
    width: 100%;
    min-width: 7rem;
  }

  .keywords {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    margin: 0;
  }

  .kw {
    background: var(--surface-sunken);
    border-radius: 999px;
    color: var(--ink-normal);
    display: inline-block;
    font-size: 0.72rem;
    margin: 0;
    padding: 0.05rem 0.45rem;
  }

  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  /* A tag chip tints its left edge with the tag's colour when one is set. */
  .tag-kw {
    border-left: 3px solid var(--tag-color, var(--border-normal));
  }

  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    margin: 0;
  }

  .badge {
    border: 1px solid transparent;
    border-radius: 999px;
    font-size: 0.68rem;
    padding: 0.05rem 0.45rem;
    white-space: nowrap;
  }

  .badge-ok {
    background: var(--status-success-bg);
    border-color: var(--status-success-border);
    color: var(--status-success);
  }

  .badge-error {
    background: var(--status-danger-bg);
    border-color: var(--status-danger-border);
    color: var(--status-danger);
  }

  .badge-warn {
    background: var(--status-warning-bg);
    border-color: var(--status-warning-border);
    color: var(--status-warning);
  }

  .badge-info {
    background: var(--status-info-bg);
    color: var(--status-info);
  }

  .badge-muted {
    background: var(--surface-sunken);
    color: var(--ink-muted);
  }

  .empty {
    color: var(--ink-muted);
    cursor: default;
    text-align: center;
  }

  .check {
    width: 2.2rem;
    text-align: center;
  }

  .check input {
    cursor: pointer;
  }
</style>
