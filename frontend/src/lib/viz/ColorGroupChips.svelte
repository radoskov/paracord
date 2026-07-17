<!-- ColorGroupChips — the color legend as clickable chips (shared by the reference graph and the
     Visualizations-tab graphs; the Insights citation/topic graph has an equivalent inline row).
     A chip: hover = highlight that color's nodes, click = show/hide it, shift-click = show only it
     (shift-click again to show all), ctrl-click = focus that group + its neighbors. Multi-membership
     nodes obey OR semantics — see lib/viz/colorGroups.ts.
     Props: groups (ordered names), colors (aligned 1:1), hidden (set of hidden group names).
     Events: toggle {group, shiftKey, ctrlKey}, hover (group name | null on leave). -->
<script lang="ts">
  import { createEventDispatcher } from "svelte";

  export let groups: string[] = [];
  export let colors: string[] = [];
  export let hidden: Set<string> = new Set();

  const dispatch = createEventDispatcher<{
    toggle: { group: string; shiftKey: boolean; ctrlKey: boolean };
    hover: string | null;
  }>();
</script>

{#if groups.length}
  <div class="chips" role="group" aria-label="Color groups">
    {#each groups as group, i (group)}
      <button
        type="button"
        class="chip"
        class:off={hidden.has(group)}
        on:click={(e) =>
          dispatch("toggle", { group, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey || e.metaKey })}
        on:mouseenter={() => dispatch("hover", group)}
        on:mouseleave={() => dispatch("hover", null)}
        title="Hover: highlight this group · Click: show/hide it · Shift-click: show only this group (shift-click again to show all) · Ctrl-click: focus this group + its neighbors"
      >
        <span class="dot" style={`background:${colors[i]}`}></span>{group}
      </button>
    {/each}
  </div>
{/if}

<style>
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    margin: 0.35rem 0;
  }
  .chip {
    align-items: center;
    background: var(--surface-overlay);
    border: 1px solid var(--border-normal);
    border-radius: 999px;
    color: var(--ink-strong);
    cursor: pointer;
    display: inline-flex;
    font-size: 0.78rem;
    font-weight: 600;
    gap: 0.3rem;
    min-height: 0;
    padding: 0.1rem 0.55rem;
  }
  .chip.off {
    opacity: 0.45;
  }
  .chip.off .dot {
    background: var(--border-normal) !important;
  }
  .chip .dot {
    border-radius: 50%;
    display: inline-block;
    height: 0.65rem;
    width: 0.65rem;
  }
</style>
