<script lang="ts">
  import { EXPORT_FORMATS, type ExportFormat } from '../api/client';

  export let label = '';
  export let disabled = false;
  export let onExport: (format: ExportFormat) => void | Promise<void> = () => {};

  let format: ExportFormat = 'bibtex';
</script>

<div class="export">
  <label>
    Export {label}
    <select bind:value={format} {disabled}>
      {#each EXPORT_FORMATS as option (option.value)}
        <option value={option.value}>{option.label}</option>
      {/each}
    </select>
  </label>
  <button type="button" on:click={() => onExport(format)} {disabled}>Export</button>
</div>

<style>
  .export {
    align-items: end;
    display: flex;
    gap: 0.5rem;
  }

  label {
    display: flex;
    flex-direction: column;
    font-size: 0.8rem;
    gap: 0.25rem;
  }
</style>
