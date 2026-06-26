<script lang="ts">
  import { onMount } from 'svelte';
  import { ApiClient } from './api/client';
  import AdminPage from './pages/AdminPage.svelte';
  import LibraryPage from './pages/LibraryPage.svelte';

  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';

  let token = '';
  let hash = '#library';

  $: adminClient = new ApiClient(apiBaseUrl, token || null);

  onMount(() => {
    hash = window.location.hash || '#library';
    const onHashChange = (): void => {
      hash = window.location.hash || '#library';
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  });
</script>

<main>
  <header>
    <div class="brand">
      <h1>PaRacORD</h1>
    </div>
    {#if token}
      <nav>
        <a href="#library" class:active={hash !== '#admin'}>Library</a>
        <a href="#admin" class:active={hash === '#admin'}>Admin</a>
      </nav>
    {/if}
  </header>

  {#if hash === '#admin' && token}
    <AdminPage client={adminClient} />
  {:else}
    <LibraryPage bind:token />
  {/if}
</main>

<style>
  :global(*) {
    box-sizing: border-box;
  }

  :global(body) {
    background: #eef1f4;
    color: #203142;
    font-family:
      Inter,
      ui-sans-serif,
      system-ui,
      -apple-system,
      BlinkMacSystemFont,
      "Segoe UI",
      sans-serif;
    margin: 0;
  }

  main {
    min-height: 100vh;
    padding: 1.25rem;
  }

  header {
    align-items: center;
    display: flex;
    justify-content: space-between;
    margin: 0 auto 1rem;
    max-width: 92rem;
  }

  .brand {
    align-items: baseline;
    display: flex;
    gap: 0.65rem;
  }

  h1 {
    font-size: 1.45rem;
    letter-spacing: 0;
    margin: 0;
  }

  nav {
    display: flex;
    gap: 0.25rem;
  }

  nav a {
    border-radius: 6px;
    color: #64717f;
    font-size: 0.875rem;
    font-weight: 600;
    padding: 0.35rem 0.75rem;
    text-decoration: none;
  }

  nav a:hover {
    background: #e2e8f0;
    color: #203142;
  }

  nav a.active {
    background: #203142;
    color: white;
  }

  :global(section) {
    max-width: 92rem;
  }
</style>
