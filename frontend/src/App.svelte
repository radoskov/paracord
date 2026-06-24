<script lang="ts">
  import LibraryPage from './pages/LibraryPage.svelte';
</script>

<main>
  <header>
    <div>
      <h1>PaperRacks</h1>
      <p>Library</p>
    </div>
  </header>
  <LibraryPage />
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

  header div {
    align-items: baseline;
    display: flex;
    gap: 0.65rem;
  }

  h1,
  p {
    margin: 0;
  }

  h1 {
    font-size: 1.45rem;
    letter-spacing: 0;
  }

  p {
    color: #64717f;
    font-size: 0.9rem;
    font-weight: 700;
  }

  :global(section) {
    max-width: 92rem;
  }
</style>
