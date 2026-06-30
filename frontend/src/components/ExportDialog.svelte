<script lang="ts">
  import {
    CITATION_STYLES,
    EXPORT_FORMATS,
    type ExportFormat,
    type ExportResponse,
  } from '../api/client';

  export let label = '';
  export let disabled = false;
  // Legacy mode: parent performs the export/download itself.
  export let onExport: (format: ExportFormat) => void | Promise<void> = () => {};
  // Rich mode: when provided, this dialog fetches and offers Preview / Copy / Download.
  export let fetchExport: ((format: ExportFormat, style?: string) => Promise<ExportResponse>) | null =
    null;

  let format: ExportFormat = 'bibtex';
  let style = 'apa';
  let preview = '';
  let status = '';
  let busy = false;

  async function fetchContent(): Promise<ExportResponse | null> {
    if (!fetchExport) return null;
    busy = true;
    status = '';
    try {
      return await fetchExport(format, style);
    } catch (error) {
      status = error instanceof Error ? error.message : 'Export failed';
      return null;
    } finally {
      busy = false;
    }
  }

  function download(r: ExportResponse): void {
    const url = URL.createObjectURL(new Blob([r.content], { type: r.content_type }));
    const a = document.createElement('a');
    a.href = url;
    a.download = r.filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function doPreview(): Promise<void> {
    const r = await fetchContent();
    if (r) {
      preview = r.content;
      status = `${r.filename} — ${r.content.length} chars`;
    }
  }

  async function doCopy(): Promise<void> {
    const r = await fetchContent();
    if (!r) return;
    preview = r.content;
    try {
      await navigator.clipboard.writeText(r.content);
      status = 'Copied to clipboard';
    } catch {
      status = 'Copy unavailable — content shown in the preview below';
    }
  }

  async function doDownload(): Promise<void> {
    const r = await fetchContent();
    if (r) {
      download(r);
      status = `Downloaded ${r.filename}`;
    }
  }
</script>

<div class="export">
  <div class="row">
    <label>
      Export {label}
      <select bind:value={format} disabled={disabled || busy} title="Citation export format">
        {#each EXPORT_FORMATS as option (option.value)}
          <option value={option.value}>{option.label}</option>
        {/each}
      </select>
    </label>
    {#if format === 'styled'}
      <label>Style
        <select bind:value={style} disabled={disabled || busy} title="Citation style for the formatted output">
          {#each CITATION_STYLES as s}<option value={s}>{s.toUpperCase()}</option>{/each}
        </select>
      </label>
    {/if}
    {#if fetchExport}
      <button type="button" class="secondary" on:click={doPreview} disabled={disabled || busy} title="Show the export inline below">Preview</button>
      <button type="button" class="secondary" on:click={doCopy} disabled={disabled || busy} title="Copy the export to the clipboard">Copy</button>
      <button type="button" on:click={doDownload} disabled={disabled || busy} title="Download the export as a file">Download</button>
    {:else}
      <button type="button" on:click={() => onExport(format)} disabled={disabled || busy} title="Export in the chosen format">Export</button>
    {/if}
  </div>
  {#if status}<p class="status">{status}</p>{/if}
  {#if preview}
    <textarea class="preview" readonly rows="8">{preview}</textarea>
  {/if}
</div>

<style>
  .export {
    display: grid;
    gap: 0.4rem;
  }

  .row {
    align-items: end;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  label {
    display: flex;
    flex-direction: column;
    font-size: 0.8rem;
    gap: 0.25rem;
  }

  .status {
    color: #64717f;
    font-size: 0.8rem;
    margin: 0;
  }

  .preview {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.78rem;
    width: 100%;
  }
</style>
