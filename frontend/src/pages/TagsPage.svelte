<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiClient, type Tag } from '../api/client';
  import { canEdit, isEditor, INSUFFICIENT_ROLE } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;
  // Set by App when the Tags tab is showing; focuses the new-tag name box on tab entry (#6).
  export let visible = true;

  let nameInput: HTMLInputElement | undefined;
  let wasVisible = false;
  $: if (visible && !wasVisible) {
    wasVisible = true;
    queueMicrotask(() => nameInput?.focus());
  } else if (!visible) {
    wasVisible = false;
  }

  let tags: Tag[] = [];
  let newTagName = '';
  let newTagColor = '';
  let newTagDescription = '';
  let loading = false;
  let message = '';

  // Inline edit state: the tag currently being renamed/re-coloured/re-described, if any.
  let editingId: string | null = null;
  let editName = '';
  let editColor = '';
  let editDescription = '';

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
      await client.createTag({
        name: newTagName,
        color: newTagColor || undefined,
        description: newTagDescription || undefined,
      });
      newTagName = '';
      newTagColor = '';
      newTagDescription = '';
      tags = await client.listTags();
    }, 'Tag created');
  }

  function startEdit(tag: Tag): void {
    editingId = tag.id;
    editName = tag.name;
    editColor = tag.color ?? '';
    editDescription = tag.description ?? '';
  }

  function cancelEdit(): void {
    editingId = null;
  }

  async function saveEdit(): Promise<void> {
    if (!editingId || !editName.trim()) return;
    const id = editingId;
    await run(async () => {
      await client.updateTag(id, {
        name: editName.trim(),
        color: editColor.trim() || null,
        description: editDescription.trim() || null,
      });
      editingId = null;
      tags = await client.listTags();
    }, 'Tag updated');
  }

  async function removeTag(tag: Tag): Promise<void> {
    if (!window.confirm(`Delete tag “${tag.name}”? It will be removed from every paper, shelf ` +
        `and rack it is applied to. This can't be undone.`))
      return;
    await run(async () => {
      await client.deleteTag(tag.id);
      if (editingId === tag.id) editingId = null;
      tags = await client.listTags();
    }, 'Tag deleted');
  }
</script>

<section class="layout">
  {#if message}<p class="muted">{message}</p>{/if}

  <div class="card">
    <h2>New tag</h2>
    <p class="muted">
      Tags are applied to a paper from its detail panel in the Library (and to shelves/racks from
      their pages). Here you create and review the available tags.
    </p>
    <form on:submit|preventDefault={createTag} class="new-tag">
      <input bind:this={nameInput} bind:value={newTagName} placeholder="Tag name" aria-label="Tag name" />
      <input bind:value={newTagColor} placeholder="#color (optional)" aria-label="Tag colour" />
      <input bind:value={newTagDescription} placeholder="Description (optional)" aria-label="Tag description" />
      <button type="submit" disabled={!newTagName.trim() || loading}
        title={newTagName.trim() ? 'Create this tag' : 'Enter a tag name first'}>Create tag</button>
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
            {#if editingId === tag.id}
              <form on:submit|preventDefault={saveEdit} class="edit-tag">
                <input bind:value={editName} aria-label="Edit tag name" placeholder="Tag name" />
                <input bind:value={editColor} aria-label="Edit tag colour" placeholder="#color (optional)" />
                <input bind:value={editDescription} aria-label="Edit tag description" placeholder="Description (optional)" />
                <div class="edit-actions">
                  <button type="submit" disabled={!editName.trim() || loading}>Save</button>
                  <button type="button" class="secondary" on:click={cancelEdit} disabled={loading}>Cancel</button>
                </div>
              </form>
            {:else}
              <span class="dot" style={`background:${tag.color ?? 'var(--ink-muted)'}`}></span>
              <strong>{tag.name}</strong>
              {#if tag.description}<small class="muted">{tag.description}</small>{/if}
              <span class="row-actions">
                <button type="button" class="secondary small" on:click={() => startEdit(tag)}
                  disabled={loading || !$canEdit}
                  title={$canEdit ? 'Rename or edit this tag' : INSUFFICIENT_ROLE}>Edit</button>
                <button type="button" class="danger small" on:click={() => removeTag(tag)}
                  disabled={loading || !$isEditor}
                  title={$isEditor ? 'Delete this tag everywhere it is applied' : INSUFFICIENT_ROLE}>Delete</button>
              </span>
            {/if}
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

  .row-actions {
    display: flex;
    gap: 0.4rem;
    margin-left: auto;
  }

  .small {
    min-height: 1.8rem;
    padding: 0.15rem 0.5rem;
  }

  .edit-tag {
    display: grid;
    gap: 0.4rem;
    grid-template-columns: minmax(0, 1fr) minmax(0, 10rem) minmax(0, 1fr) auto;
    width: 100%;
  }

  .edit-tag input {
    min-width: 0;
  }

  .edit-actions {
    display: flex;
    gap: 0.4rem;
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
