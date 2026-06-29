<script lang="ts">
  import type { ReadingStatus, Work } from '../api/client';

  export let works: Work[] = [];
  export let selectedWorkId: string | null = null;
  export let compact = false;

  // Optional multi-select (checkbox column). Off by default so other callers are unaffected.
  export let selectable = false;
  export let selectedIds: string[] = [];
  export let allSelected = false;
  export let onToggleSelect: (id: string) => void = () => {};
  export let onToggleAll: (checked: boolean) => void = () => {};

  export let onSelect: (work: Work) => void | Promise<void> = () => {};
  export let onStatusChange: (work: Work, status: ReadingStatus) => void = () => {};

  $: colCount = (compact ? 4 : 5) + (selectable ? 1 : 0);

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
      <th>Title</th>
      <th>Year</th>
      <th>Venue</th>
      <th>Status</th>
      {#if !compact}
        <th>DOI</th>
      {/if}
    </tr>
  </thead>
  <tbody>
    {#if works.length === 0}
      <tr>
        <td colspan={colCount} class="empty">No papers</td>
      </tr>
    {:else}
      {#each works as work}
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
                aria-label="Select paper"
              />
            </td>
          {/if}
          <td>
            <strong>{titleFor(work)}</strong>
            {#if work.arxiv_id}
              <span>{work.arxiv_id}</span>
            {/if}
          </td>
          <td>{work.year ?? '-'}</td>
          <td>{work.venue ?? '-'}</td>
          <td on:click|stopPropagation>
            <select
              value={work.reading_status}
              on:change={(event) =>
                onStatusChange(work, event.currentTarget.value as ReadingStatus)}
            >
              {#each readingStatuses as status}
                <option value={status}>{status}</option>
              {/each}
            </select>
          </td>
          {#if !compact}
            <td>{work.doi ?? '-'}</td>
          {/if}
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
    border-bottom: 1px solid #d7dde5;
    padding: 0.66rem 0.7rem;
    text-align: left;
    vertical-align: middle;
  }

  th {
    color: #526070;
    font-size: 0.76rem;
    font-weight: 700;
    text-transform: uppercase;
  }

  tr {
    cursor: pointer;
  }

  tbody tr:hover,
  tbody tr.selected {
    background: #edf4ef;
  }

  strong,
  span {
    display: block;
    overflow-wrap: anywhere;
  }

  span {
    color: #64717f;
    font-size: 0.78rem;
    margin-top: 0.18rem;
  }

  select {
    width: 100%;
    min-width: 7rem;
  }

  .compact th:nth-child(3),
  .compact td:nth-child(3) {
    display: none;
  }

  .empty {
    color: #6f7b88;
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
