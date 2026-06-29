<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiClient, type Tag } from '../api/client';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  let tags: Tag[] = [];
  let newTagName = '';
  let newTagColor = '';
  let loading = false;
  let message = '';

  onMount(load);

  async function run(fn: () => Promise<void>, ok?: string): Promise<void> {
    loading = true;
    message = '';
    try {
      await fn();
      if (ok) message = ok;
    } catch (error) {
      message = errorMessage(error);
    } finally {
      loading = false;
    }
  }

  async function load(): Promise<void> {
    await run(async () => {
      tags = await client.listTags();
    });
  }

  async function createTag(): Promise<void> {
    await run(async () => {
      await client.createTag({ name: newTagName, color: newTagColor || undefined });
      newTagName = '';
      newTagColor = '';
      tags = await client.listTags();
    }, 'Tag created');
  }
</script>

<section class="layout">
  {#if message}<p class="muted">{message}</p>{/if}

  <div class="card">
    <h2>New tag</h2>
    <p class="muted">
      Tags are applied to a work from its detail panel in the Library (and to shelves/racks from
      their pages). Here you create and review the available tags.
    </p>
    <form on:submit|preventDefault={createTag} class="new-tag">
      <input bind:value={newTagName} placeholder="Tag name" aria-label="Tag name" />
      <input bind:value={newTagColor} placeholder="#color (optional)" aria-label="Tag colour" />
      <button type="submit" disabled={!newTagName.trim() || loading}>Create tag</button>
    </form>
    {#if !newTagName.trim()}<p class="hintline">Enter a name to enable “Create tag”.</p>{/if}
  </div>

  <div class="card">
    <div class="head">
      <h2>Tags</h2>
      <span class="muted">{tags.length}</span>
    </div>
    {#if tags.length === 0}
      <p class="empty">No tags yet — create one above.</p>
    {:else}
      <ul class="tag-list">
        {#each tags as tag (tag.id)}
          <li>
            <span class="dot" style={`background:${tag.color ?? '#94a3b8'}`}></span>
            <strong>{tag.name}</strong>
            {#if tag.description}<small class="muted">{tag.description}</small>{/if}
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</section>

<style>
  .layout {
    display: grid;
    gap: 1rem;
  }

  .head {
    align-items: center;
    display: flex;
    justify-content: space-between;
  }

  h2 {
    font-size: 1.05rem;
    margin: 0 0 0.5rem;
  }

  .head h2 {
    margin: 0;
  }

  .new-tag {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(0, 1fr) minmax(0, 12rem) auto;
  }

  .tag-list {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
  }

  .tag-list li {
    align-items: center;
    display: flex;
    gap: 0.5rem;
  }

  .dot {
    border-radius: 50%;
    height: 0.8rem;
    width: 0.8rem;
  }

  @media (max-width: 640px) {
    .new-tag {
      grid-template-columns: 1fr;
    }
  }
</style>
