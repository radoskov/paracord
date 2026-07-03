<script lang="ts">
  import type { ReadingStatus, Work, WorkSortKey } from '../api/client';
  import { type ColumnDef, type ColumnId, LIBRARY_COLUMNS } from '../lib/columns';
  import { canModifyWork, currentUser, INSUFFICIENT_ROLE } from '../lib/session';
  import { formatDate } from '../lib/ui';

  export let works: Work[] = [];
  export let selectedWorkId: string | null = null;
  export let compact = false;

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
          class:selected={work.id === selectedWorkId}
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
