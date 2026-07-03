<script lang="ts">
  import {
    type ColumnId,
    LIBRARY_COLUMNS,
    SOFT_COLUMN_CAP,
  } from '../lib/columns';
  import Modal from './Modal.svelte';

  export let order: ColumnId[] = [];
  export let visible: ColumnId[] = [];
  export let onApply: (next: { order: ColumnId[]; visible: ColumnId[] }) => void = () => {};
  export let onClose: () => void = () => {};

  const META = new Map(LIBRARY_COLUMNS.map((c) => [c.id, c]));

  // Work on local copies so cancel doesn't mutate the page state.
  let localOrder: ColumnId[] = [...order];
  let localVisible: ColumnId[] = [...visible];

  function isVisible(id: ColumnId): boolean {
    return localVisible.includes(id);
  }

  function toggle(id: ColumnId): void {
    if (META.get(id)?.alwaysOn) return; // title can't be hidden
    localVisible = isVisible(id)
      ? localVisible.filter((x) => x !== id)
      : [...localVisible, id];
  }

  function move(index: number, delta: number): void {
    const target = index + delta;
    if (target < 0 || target >= localOrder.length) return;
    const next = [...localOrder];
    [next[index], next[target]] = [next[target], next[index]];
    localOrder = next;
  }

  function apply(): void {
    // Keep the visible list in the canonical order sequence.
    const visibleSet = new Set(localVisible);
    const orderedVisible = localOrder.filter((id) => visibleSet.has(id));
    onApply({ order: [...localOrder], visible: orderedVisible });
    onClose();
  }

  $: visibleCount = localVisible.length;
  $: overCap = visibleCount > SOFT_COLUMN_CAP;
</script>

<Modal title="Columns" {onClose}>
  <p class="muted">Choose which columns show and drag-free reorder them. Title is always shown.</p>
  <ul class="cols">
    {#each localOrder as id, i (id)}
      <li>
        <label class="row">
          <input
            type="checkbox"
            checked={isVisible(id)}
            disabled={META.get(id)?.alwaysOn}
            title={META.get(id)?.alwaysOn
              ? 'The title column is always shown'
              : `Show or hide the ${META.get(id)?.label ?? id} column`}
            on:change={() => toggle(id)}
          />
          <span class="name">{META.get(id)?.label ?? id}</span>
          {#if META.get(id)?.alwaysOn}<span class="locked">always</span>{/if}
        </label>
        <span class="reorder">
          <button type="button" class="secondary" on:click={() => move(i, -1)} disabled={i === 0}
            title={i === 0 ? 'Already the first column' : 'Move this column up'}
            aria-label="Move {META.get(id)?.label ?? id} up">↑</button>
          <button type="button" class="secondary" on:click={() => move(i, 1)}
            disabled={i === localOrder.length - 1}
            title={i === localOrder.length - 1 ? 'Already the last column' : 'Move this column down'}
            aria-label="Move {META.get(id)?.label ?? id} down">↓</button>
        </span>
      </li>
    {/each}
  </ul>
  {#if overCap}
    <p class="warn">
      {visibleCount} columns selected — more than {SOFT_COLUMN_CAP} can crowd the list on narrow
      screens. You can still apply it.
    </p>
  {/if}
  <div class="actions">
    <button type="button" on:click={apply} title="Apply this column layout to the list">Apply</button>
    <button type="button" class="secondary" on:click={onClose}
      title="Discard changes and close">Cancel</button>
  </div>
</Modal>

<style>
  .cols {
    display: grid;
    gap: 0.3rem;
    list-style: none;
    margin: 0.5rem 0;
    padding: 0;
  }

  li {
    align-items: center;
    border: 1px solid var(--border-normal);
    border-radius: 6px;
    display: flex;
    justify-content: space-between;
    padding: 0.4rem 0.6rem;
  }

  .row {
    align-items: center;
    cursor: pointer;
    display: flex;
    gap: 0.5rem;
  }

  .name {
    font-weight: 600;
  }

  .locked {
    color: var(--ink-muted);
    font-size: 0.72rem;
    text-transform: uppercase;
  }

  .reorder {
    display: flex;
    gap: 0.25rem;
  }

  .reorder button {
    min-height: 1.8rem;
    padding: 0.1rem 0.5rem;
  }

  .warn {
    background: var(--status-warning-bg);
    border: 1px solid var(--status-warning-border);
    border-radius: 6px;
    color: var(--status-warning);
    font-size: 0.85rem;
    padding: 0.4rem 0.6rem;
  }

  .actions {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }
</style>
