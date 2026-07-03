<script lang="ts">
  import { onMount } from 'svelte';

  import { ApiClient } from './api/client';
  import AdminPage from './pages/AdminPage.svelte';
  import AiModelsPage from './pages/AiModelsPage.svelte';
  import DuplicatesPage from './pages/DuplicatesPage.svelte';
  import EventsPage from './pages/EventsPage.svelte';
  import ImportPage from './pages/ImportPage.svelte';
  import InsightsPage from './pages/InsightsPage.svelte';
  import JobsPage from './pages/JobsPage.svelte';
  import LibraryPage from './pages/LibraryPage.svelte';
  import ProfilePage from './pages/ProfilePage.svelte';
  import RacksPage from './pages/RacksPage.svelte';
  import SearchPage from './pages/SearchPage.svelte';
  import ShelvesPage from './pages/ShelvesPage.svelte';
  import TagsPage from './pages/TagsPage.svelte';
  import VisualizationsPage from './pages/VisualizationsPage.svelte';
  import CitationSummaryPage from './pages/CitationSummaryPage.svelte';
  import { currentUser } from './lib/session';
  import type { UserRole } from './api/client';

  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000';

  type Tab = { id: string; label: string; hint: string; roles?: UserRole[] };

  const TABS: Tab[] = [
    { id: 'library', label: 'Library', hint: 'Search, read, edit and organise your papers.' },
    { id: 'search', label: 'Search', hint: 'Search the whole library by keyword or meaning, and act on the results.' },
    { id: 'import', label: 'Import', hint: 'Add papers from a folder, a PDF upload, an arXiv/DOI identifier, or a bibliography file.', roles: ['owner', 'admin', 'librarian', 'editor', 'contributor'] },
    { id: 'shelves', label: 'Shelves', hint: 'Group related papers into shelves.' },
    { id: 'racks', label: 'Racks', hint: 'Group related shelves into racks.' },
    { id: 'tags', label: 'Tags', hint: 'Create tags and apply them to papers, shelves or racks.' },
    { id: 'duplicates', label: 'Duplicates', hint: 'Review and resolve duplicate / version candidates.', roles: ['owner', 'admin', 'librarian', 'editor', 'contributor'] },
    { id: 'jobs', label: 'Jobs', hint: 'Background extraction & enrichment job status (and worker availability).', roles: ['owner', 'admin', 'librarian', 'editor', 'contributor'] },
    { id: 'insights', label: 'Insights', hint: 'Citation graph, topics, semantic search and scope summaries.' },
    { id: 'visualizations', label: 'Visualizations', hint: 'Explore your library visually — the temporal citation map and more.' },
    { id: 'citation-summary', label: 'Citation summary', hint: 'Scoped citation analytics — most-cited, missing, bridge and isolated papers, and a year distribution.' },
    { id: 'admin', label: 'Admin', hint: 'Manage users and agents.', roles: ['owner', 'admin'] },
    { id: 'ai', label: 'AI & Models', hint: 'Choose the engines for semantic search, summaries and topics, and manage local models.', roles: ['owner', 'admin'] },
    { id: 'events', label: 'Events', hint: 'Browse the audit log of activity across the library.', roles: ['owner', 'admin'] },
    { id: 'profile', label: 'Profile', hint: 'Your account, appearance name and password.' },
  ];

  // Tabs visible to the current role (owner-only tabs are hidden from everyone else).
  $: visibleTabs = TABS.filter((tab) => !tab.roles || ($currentUser != null && tab.roles.includes($currentUser.role)));

  let token = '';
  let username = '';
  let password = '';
  let loginError = '';
  let loggingIn = false;
  let active = 'library';
  // Explanation shown on the login screen after an involuntary sign-out (session expired / disabled).
  let sessionEndedMessage = '';

  // Tab caching (#9): tabs are lazy-mounted on first visit, then kept mounted for the session and
  // toggled with CSS (`hidden`) so their state (scroll, in-progress searches, modelled topics, open
  // modals) survives switching away and back. `visited` tracks which tabs have been mounted at least
  // once. Reset on logout/reload is intentional.
  let visited = new Set<string>();
  $: if (token && active) visited = new Set(visited).add(active);

  // A 401 on an authenticated call means the session ended server-side (expired, signed out, or the
  // account was disabled). Force a clean logout and explain why, on the next interaction.
  function onUnauthorized(detail: string): void {
    if (!token) return;
    sessionEndedMessage = detail || 'Your session has ended. Please sign in again.';
    clearSession();
  }

  // A "queue is full" rejection (D39) from any job-creating action surfaces one consistent toast.
  let queueFullMessage = '';
  let queueFullTimer: ReturnType<typeof setTimeout> | null = null;
  function onQueueFull(detail: string): void {
    queueFullMessage =
      detail || 'Processing queue is full — please wait and try again shortly.';
    if (queueFullTimer) clearTimeout(queueFullTimer);
    queueFullTimer = setTimeout(() => (queueFullMessage = ''), 8000);
  }
  function dismissQueueFull(): void {
    if (queueFullTimer) clearTimeout(queueFullTimer);
    queueFullMessage = '';
  }

  $: client = new ApiClient(apiBaseUrl, token || null, onUnauthorized, onQueueFull);
  $: activeTab = visibleTabs.find((tab) => tab.id === active) ?? visibleTabs[0] ?? TABS[0];

  // Load the signed-in profile whenever the token changes; a failure clears the session.
  let loadedFor = '';
  $: if (token && token !== loadedFor) {
    loadedFor = token;
    void loadMe();
  }

  async function loadMe(): Promise<void> {
    try {
      currentUser.set(await client.getMe());
    } catch {
      // onUnauthorized already handled a 401; any other failure also means we can't proceed.
      currentUser.set(null);
    }
  }

  onMount(() => {
    token = window.localStorage.getItem('paracord_token') ?? '';
    syncHash();
    const onHash = (): void => syncHash();
    window.addEventListener('hashchange', onHash);
    window.addEventListener('keydown', onKeydown);
    return () => {
      window.removeEventListener('hashchange', onHash);
      window.removeEventListener('keydown', onKeydown);
    };
  });

  // Arrow-key tab navigation (#23): Left/Right move between the visible, role-filtered tabs — but
  // only when the user isn't typing (focus not in an input/textarea/contenteditable/select).
  function onKeydown(event: KeyboardEvent): void {
    if (!token) return;
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const el = document.activeElement as HTMLElement | null;
    if (el) {
      const tag = el.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable) return;
    }
    const tabs = visibleTabs;
    const current = tabs.findIndex((tab) => tab.id === active);
    if (current === -1) return;
    const next = event.key === 'ArrowLeft' ? current - 1 : current + 1;
    if (next < 0 || next >= tabs.length) return;
    event.preventDefault();
    window.location.hash = `#${tabs[next].id}`;
  }

  function syncHash(): void {
    const id = (window.location.hash || '#library').slice(1);
    active = TABS.some((tab) => tab.id === id) ? id : 'library';
  }

  // If the active tab becomes unavailable for this role (e.g. an owner-only tab after a role
  // change), fall back to Library rather than rendering nothing.
  $: if ($currentUser && active && !visibleTabs.some((tab) => tab.id === active)) {
    active = 'library';
    if (typeof window !== 'undefined') window.location.hash = '#library';
  }

  async function login(): Promise<void> {
    loggingIn = true;
    loginError = '';
    sessionEndedMessage = '';
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

  function clearSession(): void {
    token = '';
    loadedFor = '';
    // Drop cached tabs so a fresh sign-in starts clean (state reset on logout is intended, #9).
    visited = new Set<string>();
    currentUser.set(null);
    window.localStorage.removeItem('paracord_token');
  }

  async function logout(): Promise<void> {
    // Best-effort server-side revoke; the local session is cleared regardless.
    try {
      await client.logout();
    } catch {
      /* token may already be invalid */
    }
    clearSession();
  }
</script>

<main>
  {#if queueFullMessage}
    <div class="queue-toast" role="alert" data-testid="queue-full-toast">
      <span>{queueFullMessage}</span>
      <button type="button" class="queue-toast-close" on:click={dismissQueueFull} title="Dismiss">×</button>
    </div>
  {/if}
  <header>
    <div class="header-inner">
      <div class="brand">
        <h1>PaRacORD</h1>
        {#if token}<p>{activeTab.label}</p>{/if}
      </div>
      {#if token}
        <nav aria-label="Sections">
          {#each visibleTabs as tab}
            {#if tab.id !== 'profile'}
              <a href={`#${tab.id}`} class:active={active === tab.id} title={tab.hint}>{tab.label}</a>
            {/if}
          {/each}
          <!-- User-menu chip: the Profile link and the signed-in name grouped as one unit. -->
          <div class="user-chip">
            <a href="#profile" class:active={active === 'profile'} title="Your account, appearance name and password.">Profile</a>
            {#if $currentUser}
              <span class="whoami" title={`Signed in as ${$currentUser.username} (${$currentUser.role})`}>
                {$currentUser.display_name || $currentUser.username}
              </span>
            {/if}
          </div>
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
      {#if sessionEndedMessage}<p class="session-ended">{sessionEndedMessage}</p>{/if}
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
    <!-- Tab caching (#9): each panel is mounted on first visit, then kept mounted and hidden with
         CSS so its state survives tab switches. Pages that poll or render a graph receive a
         `visible` prop so they can pause work / resize while hidden. -->
    {#if visited.has('library')}
      <div hidden={active !== 'library'}><LibraryPage {client} /></div>
    {/if}
    {#if visited.has('search')}
      <div hidden={active !== 'search'}><SearchPage {client} /></div>
    {/if}
    {#if visited.has('import')}
      <div hidden={active !== 'import'}><ImportPage {client} /></div>
    {/if}
    {#if visited.has('shelves')}
      <div hidden={active !== 'shelves'}><ShelvesPage {client} /></div>
    {/if}
    {#if visited.has('racks')}
      <div hidden={active !== 'racks'}><RacksPage {client} /></div>
    {/if}
    {#if visited.has('tags')}
      <div hidden={active !== 'tags'}><TagsPage {client} /></div>
    {/if}
    {#if visited.has('duplicates')}
      <div hidden={active !== 'duplicates'}><DuplicatesPage {client} /></div>
    {/if}
    {#if visited.has('jobs')}
      <div hidden={active !== 'jobs'}><JobsPage {client} visible={active === 'jobs'} /></div>
    {/if}
    {#if visited.has('insights')}
      <div hidden={active !== 'insights'}><InsightsPage {client} visible={active === 'insights'} /></div>
    {/if}
    {#if visited.has('visualizations')}
      <div hidden={active !== 'visualizations'}><VisualizationsPage {client} visible={active === 'visualizations'} /></div>
    {/if}
    {#if visited.has('citation-summary')}
      <div hidden={active !== 'citation-summary'}><CitationSummaryPage {client} visible={active === 'citation-summary'} /></div>
    {/if}
    {#if visited.has('admin')}
      <div hidden={active !== 'admin'}><AdminPage {client} /></div>
    {/if}
    {#if visited.has('ai')}
      <div hidden={active !== 'ai'}><AiModelsPage {client} /></div>
    {/if}
    {#if visited.has('events')}
      <div hidden={active !== 'events'}><EventsPage {client} /></div>
    {/if}
    {#if visited.has('profile')}
      <div hidden={active !== 'profile'}><ProfilePage {client} /></div>
    {/if}
  {/if}
  </div>
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

  .queue-toast {
    align-items: center;
    background: #fef2f2;
    border: 1px solid #fca5a5;
    border-radius: 8px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
    color: #7f1d1d;
    display: flex;
    font-weight: 600;
    gap: 0.75rem;
    left: 50%;
    max-width: min(38rem, calc(100vw - 2rem));
    padding: 0.7rem 1rem;
    position: fixed;
    top: 0.75rem;
    transform: translateX(-50%);
    z-index: 100;
  }

  .queue-toast-close {
    background: none;
    border: none;
    color: inherit;
    cursor: pointer;
    font-size: 1.25rem;
    line-height: 1;
    min-height: auto;
    padding: 0 0.2rem;
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

  /* User-menu chip: a bordered, rounded container grouping the Profile link + the
     signed-in name into one clearly-distinct unit, set apart from the section tabs. */
  .user-chip {
    align-items: center;
    background: #f4f6f9;
    border: 1px solid #d8dee6;
    border-radius: 999px;
    display: flex;
    gap: 0.15rem;
    margin-left: 0.5rem;
    padding: 0.15rem 0.3rem 0.15rem 0.15rem;
  }

  .whoami {
    color: #44515f;
    font-size: 0.85rem;
    font-weight: 600;
    max-width: 12rem;
    overflow: hidden;
    padding-right: 0.35rem;
    text-overflow: ellipsis;
    white-space: nowrap;
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

  .session-ended {
    background: #fef3c7;
    border: 1px solid #fcd34d;
    border-radius: 0.375rem;
    color: #92400e;
    font-size: 0.875rem;
    margin: 0 0 0.75rem;
    padding: 0.5rem 0.75rem;
  }

  .login form {
    display: grid;
    gap: 0.6rem;
  }

  /* ---- Shared control styling (consistent + WCAG-AA readable) -----
     Colours come from the role tokens (--surface-*, --ink-*, --border-*, --accent-*,
     --status-*) injected by lib/theme from the YAML theme; every foreground/background
     pair here has sufficient contrast. Components should use these instead of bare colours. */
  :global(section) {
    margin: 0 auto;
    max-width: 96rem;
  }

  :global(.card) {
    background: var(--surface-raised);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-md);
    padding: 1rem;
  }

  /* Primary button: dark fill, white label. (No blanket :hover — some components style
     their own light buttons as bare `button`, and a global dark hover would put dark text
     on a dark hover. Light buttons use .secondary, which has its own safe hover below.) */
  :global(button) {
    background: var(--accent-primary);
    border: 1px solid var(--accent-primary);
    border-radius: var(--radius-sm);
    color: var(--ink-inverse);
    cursor: pointer;
    font: inherit;
    font-weight: 600;
    min-height: 2.3rem;
    padding: 0.4rem 0.7rem;
  }

  /* Secondary button: light fill, dark label, light hover. */
  :global(button.secondary) {
    background: var(--surface-overlay);
    border-color: var(--border-normal);
    color: var(--accent-secondary);
  }

  :global(button.secondary:hover:not(:disabled)) {
    background: var(--surface-hover);
    border-color: var(--border-normal);
  }

  :global(button:disabled) {
    cursor: not-allowed;
    opacity: 0.5;
  }

  :global(input),
  :global(select),
  :global(textarea) {
    background: var(--surface-overlay);
    border: 1px solid var(--border-normal);
    border-radius: var(--radius-sm);
    font: inherit;
    min-height: 2.3rem;
    padding: 0.4rem 0.55rem;
  }

  /* Make placeholder text visually distinct from real typed input: italic + a muted warm/amber
     hue (not merely a fainter grey, which reads as real text). Normal input text is unchanged. */
  :global(input::placeholder),
  :global(textarea::placeholder) {
    color: #9a6b2f;
    font-style: italic;
    opacity: 1;
  }
  :global(input::-webkit-input-placeholder),
  :global(textarea::-webkit-input-placeholder) {
    color: #9a6b2f;
    font-style: italic;
  }
  :global(input::-moz-placeholder),
  :global(textarea::-moz-placeholder) {
    color: #9a6b2f;
    font-style: italic;
    opacity: 1;
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
