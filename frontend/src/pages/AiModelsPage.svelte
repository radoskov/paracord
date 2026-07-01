<script lang="ts">
  import { ApiClient } from '../api/client';
  import AiModelsPanel from '../components/AiModelsPanel.svelte';

  export let client: ApiClient;
</script>

<div class="ai-page">
  <AiModelsPanel {client} />
</div>

<style>
  .ai-page {
    margin: 0 auto;
    max-width: 96rem;
  }
</style>
