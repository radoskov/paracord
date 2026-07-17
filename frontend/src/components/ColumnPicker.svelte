<!-- ColumnPicker — modal for choosing which library-list columns show, their order, their width
     ratios, and whether row divider lines show.
     Props: order (full column-id sequence), visible (currently-shown column ids), widths
     (column-id → width ratio), dividers (row divider lines on/off), onApply (called with the new
     {order, visible, widths, dividers} on Apply), onClose (called on Apply and Cancel).
     Events/callbacks: none (plain prop callbacks, not Svelte events).
     Non-obvious lifecycle/state: edits local copies so Cancel doesn't mutate the caller's state;
     Apply re-derives `visible` from `localOrder` so the emitted list is always in canonical
     column order regardless of toggle sequence. Width is a RATIO (relative weight), not pixels:
     the table divides its actual width by the sum of the visible ratios. -->
<script lang="ts">
  import {
    type ColumnId,
    defaultColumnWidths,
    LIBRARY_COLUMNS,
    MAX_COLUMN_WIDTH,
    MIN_COLUMN_WIDTH,
    SOFT_COLUMN_CAP,
  } from '../lib/columns';
  import Modal from './Modal.svelte';

  export let order: ColumnId[] = [];
  export let visible: ColumnId[] = [];
  export let widths: Record<ColumnId, number> = defaultColumnWidths();
  export let dividers = true;
  export let onApply: (next: {
    order: ColumnId[];
    visible: ColumnId[];
    widths: Record<ColumnId, number>;
    dividers: boolean;
  }) => void = () => {};
  export let onClose: () => void = () => {};

  const META = new Map(LIBRARY_COLUMNS.map((c) => [c.id, c]));

  // Work on local copies so cancel doesn't mutate the page state.
  let localOrder: ColumnId[] = [...order];
  let localVisible: ColumnId[] = [...visible];
  let localWidths: Record<ColumnId, number> = { ...defaultColumnWidths(), ...widths };
  let localDividers = dividers;

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

  function clampWidth(value: number): number {
    if (!Number.isFinite(value)) return MIN_COLUMN_WIDTH;
    return Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, Math.round(value)));
  }

  function apply(): void {
    // Keep the visible list in the canonical order sequence.
    const visibleSet = new Set(localVisible);
    const orderedVisible = localOrder.filter((id) => visibleSet.has(id));
    const cleanWidths = Object.fromEntries(
      Object.entries(localWidths).map(([id, ratio]) => [id, clampWidth(Number(ratio))]),
    ) as Record<ColumnId, number>;
    onApply({
      order: [...localOrder],
      visible: orderedVisible,
      widths: cleanWidths,
      dividers: localDividers,
    });
    onClose();
  }

  $: visibleCount = localVisible.length;
  $: overCap = visibleCount > SOFT_COLUMN_CAP;
</script>

<Modal title="Columns" {onClose}>
  <p class="muted">
    Choose which columns show, their relative width, and their order. Title is always shown.
    Width is a ratio: each column gets its share of the list's actual width, so the layout adapts
    when you resize the list or change which columns show.
  </p>
  <div class="actions actions-top">
    <button type="button" on:click={apply} title="Apply this column layout to the list">Apply</button>
    <button type="button" class="secondary" on:click={onClose}
      title="Discard changes and close">Cancel</button>
    <label class="dividers">
      <input type="checkbox" bind:checked={localDividers} />
      Divider lines between rows
    </label>
  </div>
  {#if overCap}
    <p class="warn">
      {visibleCount} columns selected — more than {SOFT_COLUMN_CAP} can crowd the list on narrow
      screens. You can still apply it.
    </p>
  {/if}
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
        <span class="controls">
          <input
            type="number"
            class="width"
            min={MIN_COLUMN_WIDTH}
            max={MAX_COLUMN_WIDTH}
            step="1"
            bind:value={localWidths[id]}
            title="Width ratio for the {META.get(id)?.label ?? id} column (relative weight, not pixels)"
            aria-label="{META.get(id)?.label ?? id} width ratio"
          />
          <span class="reorder">
            <button type="button" class="secondary" on:click={() => move(i, -1)} disabled={i === 0}
              title={i === 0 ? 'Already the first column' : 'Move this column up'}
              aria-label="Move {META.get(id)?.label ?? id} up">↑</button>
            <button type="button" class="secondary" on:click={() => move(i, 1)}
              disabled={i === localOrder.length - 1}
              title={i === localOrder.length - 1 ? 'Already the last column' : 'Move this column down'}
              aria-label="Move {META.get(id)?.label ?? id} down">↓</button>
          </span>
        </span>
      </li>
    {/each}
  </ul>
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

  .controls {
    align-items: center;
    display: flex;
    gap: 0.5rem;
  }

  .width {
    width: 4.2rem;
  }

  .reorder {
    display: flex;
    gap: 0.25rem;
  }

  .reorder button {
    min-height: 1.8rem;
    padding: 0.1rem 0.5rem;
  }

  .dividers {
    align-items: center;
    display: flex;
    font-size: 0.85rem;
    gap: 0.4rem;
    margin-left: auto;
  }

  .dividers input {
    width: auto;
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

  .actions-top {
    margin-bottom: 0.5rem;
  }
</style>
