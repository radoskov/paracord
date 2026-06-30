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

  const ROLES: { role: string; label: string; blurb: string }[] = [
    { role: 'reader', label: 'Reader', blurb: 'Browse, search and read papers. Cannot modify the library.' },
    { role: 'editor', label: 'Editor', blurb: 'Everything a reader can do, plus import, edit, enrich and delete papers.' },
    { role: 'owner', label: 'Owner', blurb: 'Full administration: manage users and agents, AI settings, and the audit log.' },
  ];
</script>

{#if me}
  <div class="profile">
    <section class="card">
      <div class="head">
        <h2>Account</h2>
        <span class="role-badge role-{me.role}">{me.role}</span>
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

    <section class="card">
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
        <p class="muted">Changing your password signs out your other active sessions.</p>
      {/if}
    </section>

    <section class="card">
      <h2>Roles &amp; access</h2>
      <ul class="roles">
        {#each ROLES as r}
          <li class:current={r.role === me.role}>
            <strong>{r.label}{#if r.role === me.role} <span class="you">· your role</span>{/if}</strong>
            <span class="muted">{r.blurb}</span>
          </li>
        {/each}
      </ul>
    </section>
  </div>
{:else}
  <p class="muted">Loading your profile…</p>
{/if}

<style>
  .profile {
    display: grid;
    gap: 1rem;
    grid-template-columns: 1fr;
    max-width: 48rem;
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
  .role-badge {
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 700;
    padding: 0.15rem 0.6rem;
    text-transform: capitalize;
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
  .roles {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    list-style: none;
    margin: 0.6rem 0 0;
    padding: 0;
  }
  .roles li {
    border: 1px solid #e2e8f0;
    border-radius: 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    padding: 0.5rem 0.7rem;
  }
  .roles li.current {
    border-color: #93c5fd;
    background: #f0f7ff;
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
