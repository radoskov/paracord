<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiClient } from './api/client';
  import AdminPage from './pages/AdminPage.svelte';
  import DuplicatesPage from './pages/DuplicatesPage.svelte';
  import ImportPage from './pages/ImportPage.svelte';
  import InsightsPage from './pages/InsightsPage.svelte';
  import JobsPage from './pages/JobsPage.svelte';
  import LibraryPage from './pages/LibraryPage.svelte';
  import RacksPage from './pages/RacksPage.svelte';
  import ShelvesPage from './pages/ShelvesPage.svelte';
  import TagsPage from './pages/TagsPage.svelte';

  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';

  const TABS = [
    { id: 'library', label: 'Library', hint: 'Search, read, edit and organise your papers.' },
    { id: 'import', label: 'Import', hint: 'Add papers from a folder, a PDF upload, an arXiv/DOI identifier, or a bibliography file.' },
    { id: 'shelves', label: 'Shelves', hint: 'Group related papers into shelves.' },
    { id: 'racks', label: 'Racks', hint: 'Group related shelves into racks.' },
    { id: 'tags', label: 'Tags', hint: 'Create tags and apply them to papers, shelves or racks.' },
    { id: 'duplicates', label: 'Duplicates', hint: 'Review and resolve duplicate / version candidates.' },
    { id: 'jobs', label: 'Jobs', hint: 'Background extraction & enrichment job status (and worker availability).' },
    { id: 'insights', label: 'Insights', hint: 'Citation graph, topics, semantic search and scope summaries.' },
    { id: 'admin', label: 'Admin', hint: 'Manage users and agents, and view the audit log.' },
  ];

  let token = '';
  let username = '';
  let password = '';
  let loginError = '';
  let loggingIn = false;
  let active = 'library';

  $: client = new ApiClient(apiBaseUrl, token || null);
  $: activeTab = TABS.find((tab) => tab.id === active) ?? TABS[0];

  onMount(() => {
    token = window.localStorage.getItem('paracord_token') ?? '';
    syncHash();
    const onHash = (): void => syncHash();
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  });

  function syncHash(): void {
    const id = (window.location.hash || '#library').slice(1);
    active = TABS.some((tab) => tab.id === id) ? id : 'library';
  }

  async function login(): Promise<void> {
    loggingIn = true;
    loginError = '';
    try {
      token = await new ApiClient(apiBaseUrl).login(username, password);
      window.localStorage.setItem('paracord_token', token);
      password = '';
    } catch (error) {
      loginError = error instanceof Error ? error.message : 'Sign in failed';
    } finally {
      loggingIn = false;
    }
  }

  function logout(): void {
    token = '';
    window.localStorage.removeItem('paracord_token');
  }

  // Change-password modal
  let showChangePw = false;
  let curPw = '';
  let newPw = '';
  let pwMsg = '';
  let pwBusy = false;

  async function submitChangePassword(): Promise<void> {
    pwBusy = true;
    pwMsg = '';
    try {
      const result = await client.changePassword(curPw, newPw);
      pwMsg = `Password changed (${result.sessions_revoked} other session(s) signed out).`;
      curPw = newPw = '';
    } catch (error) {
      pwMsg = error instanceof Error ? error.message : 'Change failed';
    } finally {
      pwBusy = false;
    }
  }
</script>

<main>
  <header>
    <div class="header-inner">
      <div class="brand">
        <h1>PaRacORD</h1>
        {#if token}<p>{activeTab.label}</p>{/if}
      </div>
      {#if token}
        <nav aria-label="Sections">
          {#each TABS as tab}
            <a href={`#${tab.id}`} class:active={active === tab.id} title={tab.hint}>{tab.label}</a>
          {/each}
          <button type="button" class="signout secondary" on:click={() => { showChangePw = true; pwMsg = ''; }}
            title="Change your password">
            Password
          </button>
          <button type="button" class="signout" on:click={logout} title="Sign out of PaRacORD">
            Sign out
          </button>
        </nav>
      {/if}
    </div>
  </header>

  <div class="content">
  {#if !token}
    <section class="login card">
      <h2>Sign in</h2>
      <p class="muted">Sign in with the account created on the server console.</p>
      <form on:submit|preventDefault={login}>
        <label>
          Username
          <input bind:value={username} autocomplete="username" />
        </label>
        <label>
          Password
          <input type="password" bind:value={password} autocomplete="current-password" />
        </label>
        <button type="submit" disabled={loggingIn || !username || !password}>Sign in</button>
      </form>
      {#if loginError}<p class="danger">{loginError}</p>{/if}
    </section>
  {:else}
    <p class="tab-hint">{activeTab.hint}</p>
    {#if active === 'library'}
      <LibraryPage {client} />
    {:else if active === 'import'}
      <ImportPage {client} />
    {:else if active === 'shelves'}
      <ShelvesPage {client} />
    {:else if active === 'racks'}
      <RacksPage {client} />
    {:else if active === 'tags'}
      <TagsPage {client} />
    {:else if active === 'duplicates'}
      <DuplicatesPage {client} />
    {:else if active === 'jobs'}
      <JobsPage {client} />
    {:else if active === 'insights'}
      <InsightsPage {client} />
    {:else if active === 'admin'}
      <AdminPage {client} />
    {/if}
  {/if}
  </div>

  {#if token && showChangePw}
    <div class="pw-overlay" role="dialog" aria-modal="true" on:click|self={() => (showChangePw = false)}>
      <div class="pw-box card">
        <h2>Change password</h2>
        <form on:submit|preventDefault={submitChangePassword}>
          <label>Current password<input type="password" bind:value={curPw} autocomplete="current-password" /></label>
          <label>New password<input type="password" bind:value={newPw} autocomplete="new-password" /></label>
          <div class="pw-actions">
            <button type="submit" disabled={pwBusy || !curPw || newPw.length < 8}>Change</button>
            <button type="button" class="secondary" on:click={() => (showChangePw = false)}>Close</button>
          </div>
          {#if newPw && newPw.length < 8}<p class="hintline">New password must be at least 8 characters.</p>{/if}
          {#if pwMsg}<p class="muted">{pwMsg}</p>{/if}
        </form>
      </div>
    </div>
  {/if}
</main>

<style>
  :global(*) {
    box-sizing: border-box;
  }

  :global(body) {
    background: #eef1f4;
    color: #203142;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
      sans-serif;
    margin: 0;
  }

  main {
    min-height: 100vh;
  }

  header {
    background: #fbfcfd;
    border-bottom: 1px solid #d8dee6;
    position: sticky;
    top: 0;
    z-index: 30;
  }

  .header-inner {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    justify-content: space-between;
    margin: 0 auto;
    max-width: 96rem;
    padding: 0.6rem 1.25rem;
  }

  .content {
    padding: 1rem 1.25rem 1.5rem;
  }

  .brand {
    align-items: baseline;
    display: flex;
    gap: 0.65rem;
  }

  h1 {
    font-size: 1.4rem;
    margin: 0;
  }

  .brand p {
    color: #64717f;
    font-size: 0.9rem;
    font-weight: 700;
    margin: 0;
  }

  nav {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
  }

  nav a {
    border-radius: 6px;
    color: #44515f;
    font-size: 0.875rem;
    font-weight: 600;
    padding: 0.35rem 0.7rem;
    text-decoration: none;
  }

  nav a:hover {
    background: #e2e8f0;
  }

  nav a.active {
    background: #203142;
    color: white;
  }

  .signout {
    margin-left: 0.4rem;
  }

  .tab-hint {
    color: #64717f;
    font-size: 0.9rem;
    margin: 0 auto 1rem;
    max-width: 96rem;
  }

  .login {
    margin: 4rem auto 0;
    max-width: 26rem;
  }

  .pw-overlay {
    align-items: center;
    background: rgba(20, 28, 38, 0.5);
    display: flex;
    inset: 0;
    justify-content: center;
    position: fixed;
    z-index: 50;
  }

  .pw-box {
    max-width: 22rem;
    width: 92vw;
  }

  .pw-box form {
    display: grid;
    gap: 0.6rem;
    margin-top: 0.6rem;
  }

  .pw-actions {
    display: flex;
    gap: 0.5rem;
  }

  .login form {
    display: grid;
    gap: 0.6rem;
  }

  /* ---- Design tokens + shared control styling (consistent + WCAG-AA readable) -----
     Palette: slate-ish neutrals with a dark primary; every foreground/background pair
     here has sufficient contrast. Components should use these instead of bare colours. */
  :global(:root) {
    --pg-bg: #eef1f4;
    --pg-surface: #fbfcfd;
    --pg-border: #cbd5e1;
    --pg-text: #1f2a36;
    --pg-muted: #64717f;
    --pg-primary: #2d3e50; /* primary button background (white text on it = ~9:1) */
    --pg-primary-hover: #1f2a36;
    --pg-on-primary: #ffffff;
    --pg-secondary-bg: #ffffff;
    --pg-secondary-hover: #eef2f6;
    --pg-secondary-text: #21303d; /* dark text for light buttons (never white-on-white) */
    --pg-danger: #b3261e;
  }

  :global(section) {
    margin: 0 auto;
    max-width: 96rem;
  }

  :global(.card) {
    background: var(--pg-surface);
    border: 1px solid #d8dee6;
    border-radius: 8px;
    padding: 1rem;
  }

  /* Primary button: dark fill, white label. (No blanket :hover — some components style
     their own light buttons as bare `button`, and a global dark hover would put dark text
     on a dark hover. Light buttons use .secondary, which has its own safe hover below.) */
  :global(button) {
    background: var(--pg-primary);
    border: 1px solid var(--pg-primary);
    border-radius: 6px;
    color: var(--pg-on-primary);
    cursor: pointer;
    font: inherit;
    font-weight: 600;
    min-height: 2.3rem;
    padding: 0.4rem 0.7rem;
  }

  /* Secondary button: light fill, dark label, light hover. */
  :global(button.secondary) {
    background: var(--pg-secondary-bg);
    border-color: var(--pg-border);
    color: var(--pg-secondary-text);
  }

  :global(button.secondary:hover:not(:disabled)) {
    background: var(--pg-secondary-hover);
    border-color: var(--pg-border);
  }

  :global(button:disabled) {
    cursor: not-allowed;
    opacity: 0.5;
  }

  :global(input),
  :global(select),
  :global(textarea) {
    background: white;
    border: 1px solid var(--pg-border);
    border-radius: 6px;
    font: inherit;
    min-height: 2.3rem;
    padding: 0.4rem 0.55rem;
  }

  :global(label) {
    color: #526070;
    display: grid;
    font-size: 0.82rem;
    font-weight: 600;
    gap: 0.25rem;
  }

  :global(.muted) {
    color: #64717f;
    font-size: 0.86rem;
  }

  :global(.empty) {
    background: #f4f6f9;
    border: 1px dashed #cdd6e0;
    border-radius: 6px;
    color: #64717f;
    font-size: 0.9rem;
    padding: 0.9rem;
    text-align: center;
  }

  :global(.danger) {
    color: #b3261e;
  }

  :global(.hintline) {
    color: #8a96a3;
    font-size: 0.78rem;
    margin: 0.2rem 0 0;
  }
</style>
