<script lang="ts">
  import type { CitationContext } from '../api/client';

  export let fileId: string;
  export let fileName: string;
  export let fileUrl: string | null = null;
  export let contexts: CitationContext[] = [];

  let tab: 'pdf' | 'contexts' = 'pdf';
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
    </nav>
  </header>

  {#if tab === 'pdf'}
    {#if fileUrl}
      <iframe title={fileName} src={fileUrl}></iframe>
    {:else}
      <p class="empty">Open a PDF in the reader</p>
    {/if}
  {:else}
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

  .context-list {
    display: grid;
    gap: 0.65rem;
    max-height: min(72vh, 48rem);
    overflow: auto;
  }

  .context-list article {
    background: #eef2f6;
    border: 1px solid #d8dee6;
    border-radius: 6px;
    padding: 0.7rem;
  }

  .context-list article header {
    margin-bottom: 0.35rem;
  }

  .context-list p,
  .context-list small {
    overflow-wrap: anywhere;
  }

  .context-list p {
    margin: 0 0 0.35rem;
  }
</style>
