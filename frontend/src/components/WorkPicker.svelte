<script lang="ts">
  // A small typeahead for choosing another paper (issue 4: move-file target / merge source).
  // Debounced metadata search via listWorks; excludes the current paper; emits the pick via onSelect.
  import { ApiClient, type Work } from '../api/client';

  export let client: ApiClient;
  export let excludeId: string | null = null;
  export let placeholder = 'Search papers by title, DOI, or identifier…';
  export let onSelect: (work: Work) => void = () => {};

  let query = '';
  let results: Work[] = [];
  let searching = false;
  let debounce: ReturnType<typeof setTimeout> | null = null;

  function onInput(): void {
    if (debounce) clearTimeout(debounce);
    const q = query.trim();
    if (q.length < 2) {
      results = [];
      return;
    }
    debounce = setTimeout(() => void run(q), 250);
  }

  async function run(q: string): Promise<void> {
    searching = true;
    try {
      const page = await client.listWorks({ q });
      results = page.items.filter((w) => w.id !== excludeId).slice(0, 8);
    } catch {
      results = [];
    } finally {
      searching = false;
    }
  }

  function pick(work: Work): void {
    onSelect(work);
    query = '';
    results = [];
  }
</script>

<div class="work-picker">
  <input
    type="text"
    bind:value={query}
    on:input={onInput}
    {placeholder}
    aria-label={placeholder} />
  {#if searching}
    <p class="picker-note">Searching…</p>
  {:else if results.length > 0}
    <ul class="picker-results">
      {#each results as w (w.id)}
        <li>
          <button type="button" on:click={() => pick(w)} title="Select this paper">
            <span class="picker-title">{w.canonical_title || 'Untitled paper'}</span>
            <small class="picker-meta">{[w.year, w.doi].filter(Boolean).join(' · ')}</small>
          </button>
        </li>
      {/each}
    </ul>
  {:else if query.trim().length >= 2}
    <p class="picker-note">No matching papers.</p>
  {/if}
</div>

<style>
  .work-picker input {
    width: 100%;
    padding: 0.4rem 0.55rem;
    box-sizing: border-box;
  }
  .picker-note {
    margin: 0.4rem 0 0;
    font-size: 0.85rem;
    color: var(--ink-muted);
  }
  .picker-results {
    list-style: none;
    margin: 0.4rem 0 0;
    padding: 0;
    max-height: 15rem;
    overflow-y: auto;
    border: 1px solid var(--border);
    border-radius: 6px;
  }
  .picker-results li + li {
    border-top: 1px solid var(--border);
  }
  .picker-results button {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.1rem;
    width: 100%;
    text-align: left;
    background: none;
    border: none;
    padding: 0.45rem 0.6rem;
    cursor: pointer;
    color: inherit;
  }
  .picker-results button:hover {
    background: var(--surface-2, rgba(127, 127, 127, 0.12));
  }
  .picker-title {
    font-weight: 600;
    font-size: 0.9rem;
  }
  .picker-meta {
    color: var(--ink-muted);
  }
</style>
