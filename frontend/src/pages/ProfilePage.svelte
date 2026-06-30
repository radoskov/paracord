<script lang="ts">
  import { ApiClient, type CurrentUser } from '../api/client';
  import { currentUser } from '../lib/session';
  import { errorMessage } from '../lib/ui';

  export let client: ApiClient;

  // Editable fields are seeded from the store and kept local until saved.
  let displayName = '';
  let email = '';
  let seededFor: string | null = null;

  $: me = $currentUser;
  // Seed the form once per loaded user (re-seed if the signed-in account changes).
  $: if (me && me.id !== seededFor) {
    displayName = me.display_name ?? '';
    email = me.email ?? '';
    seededFor = me.id;
  }

  $: dirty = !!me && ((me.display_name ?? '') !== displayName.trim() || (me.email ?? '') !== email.trim());

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
    editor: { label: 'Editor', blurb: 'Browse, search and read papers; import, edit, enrich and delete papers.' },
    owner: {
      label: 'Owner',
      blurb:
        'Browse, search and read papers; import, edit, enrich and delete papers; and manage users, agents, AI settings and the audit log.',
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
        <div class="actions">
          <button type="submit" disabled={savingProfile || !dirty}>Save changes</button>
        </div>
        {#if profileMsg}<p class="muted">{profileMsg}</p>{/if}
        {#if profileErr}<p class="danger">{profileErr}</p>{/if}
      </form>
    </section>

    <section class="card pw">
      <div class="head">
        <h2>Password</h2>
        <button type="button" class="secondary" on:click={togglePw}>
          {showPw ? 'Cancel' : 'Change password'}
        </button>
      </div>
      {#if showPw}
        <form class="fields" on:submit|preventDefault={submitPassword}>
          <label>Current password<input type="password" bind:value={curPw} autocomplete="current-password" /></label>
          <label>New password<input type="password" bind:value={newPw} autocomplete="new-password" /></label>
          {#if newPw && newPw.length < 8}<p class="hintline">New password must be at least 8 characters.</p>{/if}
          <div class="actions">
            <button type="submit" disabled={pwBusy || !curPw || newPw.length < 8}>Change</button>
          </div>
          {#if pwMsg}<p class="muted">{pwMsg}</p>{/if}
          {#if pwErr}<p class="danger">{pwErr}</p>{/if}
        </form>
      {:else}
        <p class="muted">Signs you out everywhere else (other browsers/devices). This tab stays signed in.</p>
      {/if}
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
    .roles {
      grid-column: 1;
      grid-row: auto;
    }
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
    color: #64717f;
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
    background: #fde9d7;
    color: #9a4a07;
  }
  .role-editor {
    background: #dbeafe;
    color: #1e40af;
  }
  .role-reader {
    background: #e2e8f0;
    color: #44515f;
  }
  .role-card {
    background: #f0f7ff;
    border: 1px solid #93c5fd;
    border-radius: 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    margin-top: 0.6rem;
    padding: 0.5rem 0.7rem;
  }
  .you {
    color: #2563eb;
    font-size: 0.8rem;
  }
  .hintline {
    color: #b45309;
    font-size: 0.8rem;
    margin: 0;
  }
</style>
