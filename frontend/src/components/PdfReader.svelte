<script lang="ts">
  import type { Annotation, CitationContext } from '../api/client';

  export let fileId: string;
  export let fileName: string;
  export let fileUrl: string | null = null;
  export let contexts: CitationContext[] = [];
  export let annotations: Annotation[] = [];
  export let onCreateAnnotation:
    | ((payload: {
        annotation_type: string;
        page: number | null;
        selected_text: string | null;
        content_markdown: string | null;
      }) => Promise<void>)
    | null = null;

  let tab: 'pdf' | 'contexts' | 'annotations' = 'pdf';
  let annotationType = 'note';
  let annotationPage = '';
  let selectedText = '';
  let annotationContent = '';

  async function createAnnotation(): Promise<void> {
    if (!onCreateAnnotation) return;
    await onCreateAnnotation({
      annotation_type: annotationType,
      page: annotationPage ? Number(annotationPage) : null,
      selected_text: selectedText || null,
      content_markdown: annotationContent || null,
    });
    selectedText = '';
    annotationContent = '';
  }
</script>

<section class="reader">
  <header>
    <div>
      <h3>{fileName}</h3>
      <span>{fileId.slice(0, 8)}</span>
    </div>
    <nav aria-label="Reader panels">
      <button type="button" class:active={tab === 'pdf'} on:click={() => (tab = 'pdf')}>PDF</button>
      <button
        type="button"
        class:active={tab === 'contexts'}
        on:click={() => (tab = 'contexts')}
      >
        References
      </button>
      <button
        type="button"
        class:active={tab === 'annotations'}
        on:click={() => (tab = 'annotations')}
      >
        Notes
      </button>
    </nav>
  </header>

  {#if tab === 'pdf'}
    {#if fileUrl}
      <iframe title={fileName} src={fileUrl}></iframe>
    {:else}
      <p class="empty">Open a PDF in the reader</p>
    {/if}
  {:else if tab === 'contexts'}
    {#if contexts.length === 0}
      <p class="empty">No citation contexts extracted</p>
    {:else}
      <div class="context-list">
        {#each contexts as context}
          <article>
            <header>
              <strong>{context.marker_text ?? 'citation'}</strong>
              <span>{context.section_label ?? 'section unknown'}</span>
            </header>
            <p>{context.context_sentence ?? 'No sentence context'}</p>
            <small>
              {context.reference_title ?? context.reference_raw_citation ?? 'Unparsed reference'}
            </small>
          </article>
        {/each}
      </div>
    {/if}
  {:else}
    <form class="annotation-form" on:submit|preventDefault={createAnnotation}>
      <select bind:value={annotationType} disabled={!onCreateAnnotation}>
        <option value="note">Note</option>
        <option value="highlight">Highlight</option>
        <option value="page_anchor">Page anchor</option>
        <option value="citation_note">Citation note</option>
      </select>
      <input bind:value={annotationPage} inputmode="numeric" placeholder="Page" />
      <input bind:value={selectedText} placeholder="Selected text" />
      <textarea bind:value={annotationContent} placeholder="Note"></textarea>
      <button type="submit" disabled={!onCreateAnnotation || (!selectedText && !annotationContent)}>
        Add
      </button>
    </form>

    {#if annotations.length === 0}
      <p class="empty">No annotations</p>
    {:else}
      <div class="annotation-list">
        {#each annotations as annotation}
          <article>
            <header>
              <strong>{annotation.annotation_type.replaceAll('_', ' ')}</strong>
              <span>page {annotation.page ?? '-'}</span>
            </header>
            {#if annotation.selected_text}<p>{annotation.selected_text}</p>{/if}
            {#if annotation.content_markdown}<small>{annotation.content_markdown}</small>{/if}
          </article>
        {/each}
      </div>
    {/if}
  {/if}
</section>

<style>
  .reader {
    display: grid;
    gap: 0.7rem;
  }

  header {
    align-items: center;
    display: flex;
    gap: 0.75rem;
    justify-content: space-between;
  }

  header div {
    display: grid;
    gap: 0.2rem;
    min-width: 0;
  }

  h3 {
    color: #1f2a36;
    font-size: 1rem;
    line-height: 1.2;
    margin: 0;
    overflow-wrap: anywhere;
  }

  span,
  small,
  .empty {
    color: #667381;
  }

  span {
    font-size: 0.78rem;
  }

  nav {
    display: flex;
    gap: 0.35rem;
  }

  button {
    background: white;
    border: 1px solid #bcc7d2;
    border-radius: 6px;
    color: #21303d;
    cursor: pointer;
    font: inherit;
    font-weight: 700;
    min-height: 2rem;
    padding: 0.3rem 0.55rem;
  }

  input,
  select,
  textarea {
    border: 1px solid #bcc7d2;
    border-radius: 6px;
    font: inherit;
    min-height: 2.2rem;
    padding: 0.35rem 0.5rem;
  }

  textarea {
    min-height: 4.5rem;
    resize: vertical;
  }

  button.active {
    background: #203142;
    color: white;
  }

  iframe {
    background: #eef2f6;
    border: 1px solid #d8dee6;
    border-radius: 6px;
    height: min(72vh, 48rem);
    width: 100%;
  }

  .annotation-form {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: minmax(8rem, 10rem) minmax(5rem, 7rem) minmax(0, 1fr) auto;
  }

  .annotation-form textarea {
    grid-column: 1 / -1;
  }

  .context-list,
  .annotation-list {
    display: grid;
    gap: 0.65rem;
    max-height: min(72vh, 48rem);
    overflow: auto;
  }

  .context-list article,
  .annotation-list article {
    background: #eef2f6;
    border: 1px solid #d8dee6;
    border-radius: 6px;
    padding: 0.7rem;
  }

  .context-list article header,
  .annotation-list article header {
    margin-bottom: 0.35rem;
  }

  .context-list p,
  .context-list small,
  .annotation-list p,
  .annotation-list small {
    overflow-wrap: anywhere;
  }

  .context-list p,
  .annotation-list p {
    margin: 0 0 0.35rem;
  }

  @media (max-width: 760px) {
    .annotation-form {
      grid-template-columns: 1fr;
    }
  }
</style>
