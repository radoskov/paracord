<script lang="ts">
  import { ApiClient, type CurrentUser } from '../api/client';
  import { currentUser } from '../lib/session';
  import {
    activeThemeId,
    allThemeOptions,
    ensureThemeLoaded,
    followSystem,
    setFollowSystem,
    setTheme,
    type ThemeOption,
  } from '../lib/theme/store';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  // --- Appearance (theme picker, P3 + P4) ---
  // Data-driven from the bundled themes AND admin-uploaded custom themes (merged reactively), so a
  // new theme appears here automatically. Grouped by mode (Light / Dark), labelled by temperature.
  $: lightThemes = $allThemeOptions.filter((t: ThemeOption) => t.mode === 'light');
  $: darkThemes = $allThemeOptions.filter((t: ThemeOption) => t.mode === 'dark');
  let themeMsg = '';
  let themeErr = '';

  // Selecting a theme restyles the whole running app immediately (GUI + open charts/network),
  // caches it locally for no-flash boot, and persists it to the server profile. A custom theme's
  // full object is fetched + registered first so it applies live exactly like a bundled one.
  async function selectTheme(id: string): Promise<void> {
    themeMsg = '';
    themeErr = '';
    setFollowSystem(false);
    try {
      await ensureThemeLoaded(client, id);
      setTheme(id);
      const updated = await client.updateProfile({ theme: id });
      currentUser.set(updated);
      themeMsg = 'Theme saved.';
    } catch (error) {
      themeErr = errorMessage(error);
    }
  }

  function toggleFollowSystem(event: Event): void {
    themeMsg = '';
    themeErr = '';
    setFollowSystem((event.target as HTMLInputElement).checked);
  }

  function capitalize(s: string): string {
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  // Editable fields are seeded from the store and kept local until saved.
  let displayName = '';
  let email = '';
  // Preferred Library page size (D18). Kept as a string so an empty field means "reset to default".
  let papersPerPage = '';
  let seededFor: string | null = null;

  $: me = $currentUser;
  // Seed the form once per loaded user (re-seed if the signed-in account changes).
  $: if (me && me.id !== seededFor) {
    displayName = me.display_name ?? '';
    email = me.email ?? '';
    papersPerPage = me.papers_per_page != null ? String(me.papers_per_page) : '';
    seededFor = me.id;
  }

  // Parsed page-size (null = reset to server default); NaN/<1 is treated as "unset" for the diff.
  $: parsedPerPage =
    String(papersPerPage ?? '').trim() === ''
      ? null
      : Math.trunc(Number(papersPerPage)) || null;
  $: dirty =
    !!me &&
    ((me.display_name ?? '') !== displayName.trim() ||
      (me.email ?? '') !== email.trim() ||
      (me.papers_per_page ?? null) !== parsedPerPage);

  let savingProfile = false;
  let profileMsg = '';
  let profileErr = '';

  async function saveProfile(): Promise<void> {
    savingProfile = true;
    profileMsg = '';
    profileErr = '';
    try {
      const updated: CurrentUser = await client.updateProfile({
        display_name: displayName.trim() || null,
        email: email.trim() || null,
        papers_per_page: parsedPerPage,
      });
      currentUser.set(updated);
      profileMsg = 'Profile saved.';
    } catch (error) {
      profileErr = errorMessage(error);
    } finally {
      savingProfile = false;
    }
  }

  // --- Change password ---
  let showPw = false;
  let curPw = '';
  let newPw = '';
  let pwBusy = false;
  let pwMsg = '';
  let pwErr = '';

  function resetPwForm(): void {
    curPw = '';
    newPw = '';
    pwMsg = '';
    pwErr = '';
  }

  function togglePw(): void {
    showPw = !showPw;
    resetPwForm();
  }

  async function submitPassword(): Promise<void> {
    pwBusy = true;
    pwMsg = '';
    pwErr = '';
    try {
      const result = await client.changePassword(curPw, newPw);
      pwMsg = `Password changed (${result.sessions_revoked} other session(s) signed out).`;
      curPw = '';
      newPw = '';
    } catch (error) {
      pwErr = errorMessage(error);
    } finally {
      pwBusy = false;
    }
  }

  function formatDate(iso: string | null): string {
    return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—';
  }

  // Self-contained description of each role. Only the signed-in user's own role is shown
  // (the full privilege ladder is intentionally not advertised to every user).
  const ROLE_INFO: Record<string, { label: string; blurb: string }> = {
    reader: { label: 'Reader', blurb: 'Browse, search and read papers; cannot modify the library.' },
    contributor: {
      label: 'Contributor',
      blurb:
        'Browse, search and read papers; import, edit, enrich and delete your own papers (papers you created).',
    },
    editor: { label: 'Editor', blurb: 'Browse, search and read papers; import, edit, enrich and delete any paper you can see.' },
    librarian: {
      label: 'Librarian',
      blurb:
        'Everything an editor can do, plus create, edit and organise racks and shelves and manage their membership and access.',
    },
    admin: {
      label: 'Admin',
      blurb:
        'Browse, search and read papers; import, edit, enrich and delete papers; and manage editors/readers, agents, AI settings and the audit log. Cannot manage other admins or the owner.',
    },
    owner: {
      label: 'Owner',
      blurb:
        'The single, permanent account: everything an admin can do, plus manage admins. Cannot be disabled, deleted or role-changed.',
    },
  };
</script>

{#if me}
  <div class="profile">
    <section class="card account">
      <span class="role-badge role-{me.role} corner" title={`Your role: ${me.role}`}>{me.role}</span>
      <div class="head">
        <h2>Account</h2>
      </div>
      <dl class="meta">
        <div><dt>Username</dt><dd>{me.username} <small class="muted">(cannot be changed)</small></dd></div>
        <div><dt>Member since</dt><dd>{formatDate(me.created_at)}</dd></div>
        <div><dt>Last sign-in</dt><dd>{formatDate(me.last_login_at)}</dd></div>
      </dl>

      <form class="fields" on:submit|preventDefault={saveProfile}>
        <label>
          Appearance name
          <input bind:value={displayName} maxlength="255" placeholder="How your name is shown (optional)" />
        </label>
        <label>
          Email
          <input type="email" bind:value={email} maxlength="320" placeholder="Contact email (optional)" />
        </label>
        <label>
          Papers per page
          <input
            type="number"
            min="1"
            bind:value={papersPerPage}
            placeholder="Library page size (blank = default)"
          />
        </label>
        <div class="actions">
          <button type="submit" disabled={savingProfile || !dirty}
            title={dirty ? 'Save your appearance name and email' : 'No changes to save'}>Save changes</button>
        </div>
        {#if profileMsg}<p class="muted">{profileMsg}</p>{/if}
        {#if profileErr}<p class="danger">{profileErr}</p>{/if}
      </form>
    </section>

    <section class="card pw">
      <div class="head">
        <h2>Password</h2>
        <button type="button" class="secondary" on:click={togglePw}
          title={showPw ? 'Close the password form' : 'Change your account password'}>
          {showPw ? 'Cancel' : 'Change password'}
        </button>
      </div>
      {#if showPw}
        <form class="fields" on:submit|preventDefault={submitPassword}>
          <label>Current password<input type="password" bind:value={curPw} autocomplete="current-password" /></label>
          <label>New password<input type="password" bind:value={newPw} autocomplete="new-password" /></label>
          {#if newPw && newPw.length < 8}<p class="hintline">New password must be at least 8 characters.</p>{/if}
          <div class="actions">
            <button type="submit" disabled={pwBusy || !curPw || newPw.length < 8}
              title={!curPw
                ? 'Enter your current password'
                : newPw.length < 8
                  ? 'New password must be at least 8 characters'
                  : 'Change your password (signs out other sessions)'}>Change</button>
          </div>
          {#if pwMsg}<p class="muted">{pwMsg}</p>{/if}
          {#if pwErr}<p class="danger">{pwErr}</p>{/if}
        </form>
      {:else}
        <p class="muted">Signs you out everywhere else (other browsers/devices). This tab stays signed in.</p>
      {/if}
    </section>

    <section class="card appearance">
      <div class="head">
        <h2>Appearance</h2>
      </div>
      <p class="muted">Pick a theme. The whole app — including open charts and the citation network — restyles instantly, and your choice is remembered on this device and your account.</p>

      <label class="follow">
        <input type="checkbox" data-testid="follow-system" checked={$followSystem} on:change={toggleFollowSystem} />
        Follow system appearance
        <span class="muted">(use the light or dark variant of the selected temperature based on your OS)</span>
      </label>

      {#each [{ label: 'Light', themes: lightThemes }, { label: 'Dark', themes: darkThemes }] as group}
        <fieldset class="theme-group">
          <legend>{group.label}</legend>
          <div class="theme-grid">
            {#each group.themes as opt (opt.id)}
              <button
                type="button"
                class="theme-option"
                data-testid={`theme-option-${opt.id}`}
                class:selected={$activeThemeId === opt.id}
                aria-pressed={$activeThemeId === opt.id}
                title={`Use the ${opt.name} theme`}
                on:click={() => selectTheme(opt.id)}
              >
                <span class="swatch" style={`background:${opt.swatch.surface}`}>
                  <span class="dot primary" style={`background:${opt.swatch.primary}`}></span>
                  {#each opt.swatch.accents as accent}
                    <span class="dot" style={`background:${accent}`}></span>
                  {/each}
                </span>
                <span class="theme-meta">
                  <strong>{opt.name}</strong>
                  <span class="muted">{capitalize(opt.temperature)}</span>
                </span>
              </button>
            {/each}
          </div>
        </fieldset>
      {/each}
      {#if themeMsg}<p class="muted">{themeMsg}</p>{/if}
      {#if themeErr}<p class="danger">{themeErr}</p>{/if}
    </section>

    <section class="card roles">
      <h2>Roles &amp; access</h2>
      <div class="role-card">
        <strong>
          {ROLE_INFO[me.role]?.label ?? me.role}
          <span class="you">· your role</span>
        </strong>
        <span class="muted">{ROLE_INFO[me.role]?.blurb ?? ''}</span>
      </div>
    </section>
  </div>
{:else}
  <p class="muted">Loading your profile…</p>
{/if}

<style>
  .profile {
    align-items: start;
    display: grid;
    gap: 1rem;
    /* Account + Password stack in the main column; Roles & access is pinned top-right. */
    grid-template-columns: minmax(0, 1fr) 17rem;
    margin: 0 auto;
    max-width: 52rem;
  }
  .account {
    grid-column: 1;
    grid-row: 1;
  }
  .pw {
    grid-column: 1;
    grid-row: 2;
  }
  .appearance {
    grid-column: 1;
    grid-row: 3;
  }
  .roles {
    grid-column: 2;
    grid-row: 1;
  }
  @media (max-width: 720px) {
    .profile {
      grid-template-columns: 1fr;
    }
    .account,
    .pw,
    .appearance,
    .roles {
      grid-column: 1;
      grid-row: auto;
    }
  }
  .follow {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    font-size: 0.85rem;
    font-weight: 600;
    gap: 0.4rem;
    margin: 0.6rem 0 0.4rem;
  }
  .follow input {
    width: auto;
  }
  .follow .muted {
    font-weight: 400;
  }
  .theme-group {
    border: none;
    margin: 0.4rem 0 0;
    padding: 0;
  }
  .theme-group legend {
    color: var(--ink-muted);
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    padding: 0;
    text-transform: uppercase;
  }
  .theme-grid {
    display: grid;
    gap: 0.5rem;
    grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
    margin: 0.4rem 0 0.6rem;
  }
  .theme-option {
    align-items: center;
    background: var(--surface-raised);
    border: 1px solid var(--border-normal);
    border-radius: var(--radius-md);
    cursor: pointer;
    display: flex;
    gap: 0.6rem;
    padding: 0.5rem 0.6rem;
    text-align: left;
  }
  .theme-option:hover {
    background: var(--surface-hover);
  }
  .theme-option.selected {
    border-color: var(--accent-primary);
    box-shadow: 0 0 0 1px var(--accent-primary);
  }
  .swatch {
    align-items: center;
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    display: flex;
    flex: 0 0 auto;
    gap: 3px;
    height: 2rem;
    justify-content: center;
    padding: 0 5px;
    width: 3.2rem;
  }
  .dot {
    border-radius: 999px;
    height: 0.55rem;
    width: 0.55rem;
  }
  .dot.primary {
    height: 0.7rem;
    width: 0.7rem;
  }
  .theme-meta {
    display: flex;
    flex-direction: column;
    gap: 0.05rem;
  }
  .theme-meta .muted {
    font-size: 0.78rem;
  }
  .head {
    align-items: center;
    display: flex;
    gap: 0.75rem;
    justify-content: space-between;
  }
  h2 {
    font-size: 1.05rem;
    margin: 0;
  }
  .meta {
    display: grid;
    gap: 0.35rem;
    margin: 0.75rem 0 1rem;
  }
  .meta div {
    display: flex;
    gap: 0.5rem;
  }
  .meta dt {
    color: var(--ink-muted);
    flex: 0 0 8rem;
    font-weight: 600;
    margin: 0;
  }
  .meta dd {
    margin: 0;
  }
  .fields {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .fields label {
    display: flex;
    flex-direction: column;
    font-size: 0.85rem;
    font-weight: 600;
    gap: 0.25rem;
  }
  .fields input {
    font-weight: 400;
  }
  .actions {
    display: flex;
    gap: 0.5rem;
  }
  /* The Account card anchors its role badge in the top-right corner. */
  .account {
    position: relative;
  }
  .role-badge {
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 700;
    padding: 0.15rem 0.6rem;
    text-transform: capitalize;
  }
  .role-badge.corner {
    font-size: 0.8rem;
    padding: 0.2rem 0.7rem;
    position: absolute;
    right: 1rem;
    top: 1rem;
  }
  .role-owner {
    background: var(--status-warning-bg);
    color: var(--status-warning);
  }
  .role-admin {
    background: var(--accent-note-bg);
    color: var(--accent-note);
  }
  .role-librarian {
    background: var(--status-success-bg);
    color: var(--status-success);
  }
  .role-editor {
    background: var(--status-info-bg);
    color: var(--status-info);
  }
  .role-contributor {
    background: var(--status-warning-bg);
    color: var(--status-warning);
  }
  .role-reader {
    background: var(--surface-sunken);
    color: var(--ink-normal);
  }
  .role-card {
    background: var(--status-info-bg);
    border: 1px solid var(--status-info-border);
    border-radius: 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    margin-top: 0.6rem;
    padding: 0.5rem 0.7rem;
  }
  .you {
    color: var(--accent-link);
    font-size: 0.8rem;
  }
  .hintline {
    color: var(--status-warning);
    font-size: 0.8rem;
    margin: 0;
  }
</style>
