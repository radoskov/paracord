<script lang="ts">
  import {
    ApiClient,
    type AdminUser,
    type AgentFileRecord,
    type AgentRecord,
    type AuditEvent,
    type UserRole,
  } from '../api/client';

  export let client: ApiClient;

  let users: AdminUser[] = [];
  let agents: AgentRecord[] = [];
  let auditEvents: AuditEvent[] = [];
  let message = '';
  let loading = false;

  let openAgentId = '';
  let agentFiles: AgentFileRecord[] = [];

  let newUsername = '';
  let newPassword = '';
  let newRole: UserRole = 'reader';
  let enrollToken = '';
  let enrollExpiry = '';
  let lastApprovedToken = '';

  async function run(action: () => Promise<void>, success?: string): Promise<void> {
    loading = true;
    message = '';
    try {
      await action();
      if (success) message = success;
    } catch (error) {
      message = error instanceof Error ? error.message : 'Request failed';
    } finally {
      loading = false;
    }
  }

  async function refresh(): Promise<void> {
    await run(async () => {
      [users, agents, auditEvents] = await Promise.all([
        client.listAdminUsers(),
        client.listAgents(),
        client.listAuditEvents(50),
      ]);
    });
  }

  async function createUser(): Promise<void> {
    await run(async () => {
      await client.createAdminUser(newUsername, newPassword, newRole);
      newUsername = '';
      newPassword = '';
      newRole = 'reader';
      users = await client.listAdminUsers();
    }, 'User created');
  }

  async function changeRole(user: AdminUser, role: UserRole): Promise<void> {
    await run(async () => {
      await client.updateUserRole(user.id, role);
      users = await client.listAdminUsers();
    }, `Role updated to ${role}`);
  }

  async function disableUser(user: AdminUser): Promise<void> {
    await run(async () => {
      await client.disableUser(user.id);
      users = await client.listAdminUsers();
    }, 'User disabled');
  }

  async function enableUser(user: AdminUser): Promise<void> {
    await run(async () => {
      await client.enableUser(user.id);
      users = await client.listAdminUsers();
    }, 'User re-enabled');
  }

  async function issueEnrollToken(): Promise<void> {
    await run(async () => {
      const result = await client.issueEnrollToken();
      enrollToken = result.token;
      enrollExpiry = result.expires_at;
    }, 'Enrollment token issued — copy it now, it will not be shown again');
  }

  async function viewAgentFiles(agent: AgentRecord): Promise<void> {
    if (openAgentId === agent.id) {
      openAgentId = '';
      agentFiles = [];
      return;
    }
    await run(async () => {
      agentFiles = await client.listAgentFiles(agent.id);
      openAgentId = agent.id;
    });
  }

  async function teleport(agentId: string, file: AgentFileRecord): Promise<void> {
    await run(async () => {
      await client.requestTeleport(agentId, file.local_file_id);
      agentFiles = await client.listAgentFiles(agentId);
    }, 'Teleport requested — run the agent’s “teleport” command to push the file');
  }

  async function approveAgent(agent: AgentRecord): Promise<void> {
    await run(async () => {
      const result = await client.approveAgent(agent.id);
      lastApprovedToken = result.agent_token;
      agents = await client.listAgents();
    }, 'Agent approved — copy the token now');
  }

  $: void refresh();

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
  }
</script>

<div class="admin-layout">
  {#if message}
    <p class="message">{message}</p>
  {/if}

  <div class="admin-columns">
    <!-- Users -->
    <section class="surface admin-section">
      <div class="section-head">
        <h2>Users</h2>
        <button type="button" on:click={refresh} disabled={loading}>Refresh</button>
      </div>

      <form on:submit|preventDefault={createUser} class="stack">
        <input bind:value={newUsername} placeholder="Username" autocomplete="off" />
        <input bind:value={newPassword} type="password" placeholder="Password" autocomplete="new-password" />
        <select bind:value={newRole}>
          <option value="reader">reader</option>
          <option value="editor">editor</option>
          <option value="owner">owner</option>
        </select>
        <button type="submit" disabled={!newUsername || !newPassword || loading}>Create user</button>
      </form>

      {#if users.length === 0}
        <p class="empty">No users loaded</p>
      {:else}
        <div class="user-list">
          {#each users as user}
            <article class:disabled={!!user.disabled_at}>
              <header>
                <strong>{user.username}</strong>
                <span class="role-badge role-{user.role}">{user.role}</span>
                {#if user.disabled_at}
                  <span class="disabled-badge">disabled</span>
                {/if}
              </header>
              <div class="user-actions">
                <select
                  value={user.role}
                  on:change={(e) => changeRole(user, e.currentTarget.value as UserRole)}
                  disabled={loading || !!user.disabled_at}
                >
                  <option value="reader">reader</option>
                  <option value="editor">editor</option>
                  <option value="owner">owner</option>
                </select>
                {#if user.disabled_at}
                  <button
                    type="button"
                    on:click={() => enableUser(user)}
                    disabled={loading}
                    title="Re-enable this account"
                  >
                    Re-enable
                  </button>
                {:else}
                  <button
                    type="button"
                    on:click={() => disableUser(user)}
                    disabled={loading}
                    title="Disable this account (sign-in blocked; can be re-enabled)"
                  >
                    Disable
                  </button>
                {/if}
              </div>
              <small>Created {formatDate(user.created_at)}</small>
            </article>
          {/each}
        </div>
      {/if}
    </section>

    <!-- Agents -->
    <section class="surface admin-section">
      <h2>Agents</h2>
      <p class="muted small-help">
        A local <strong>agent</strong> runs on a workstation and indexes PDFs in folders <em>on
        that machine</em> (use this for files on your own PC — the “Server folder” import is only
        for folders on the server). Issue an enrollment token, run the agent with it, approve it
        here, then browse its files and teleport the ones you want into the library.
      </p>

      <div class="inline-action">
        <button type="button" on:click={issueEnrollToken} disabled={loading}>
          Issue enrollment token
        </button>
      </div>

      {#if enrollToken}
        <div class="token-box">
          <p>On the workstation, enroll the agent with this token (shown once):</p>
          <code>paracord-agent enroll --server &lt;server-url&gt; --token {enrollToken} --name my-workstation</code>
          <small>Expires {formatDate(enrollExpiry)} · then approve the agent below to mint its token.</small>
        </div>
      {/if}

      {#if lastApprovedToken}
        <div class="token-box">
          <p>Agent bearer token (shown once) — set it on the workstation:</p>
          <code>export PARACORD_AGENT_TOKEN={lastApprovedToken}</code>
          <small>Then run <code>paracord-agent sync &lt;folder&gt;</code> to index, and
            <code>paracord-agent teleport &lt;folder&gt;</code> to push requested files.</small>
        </div>
      {/if}

      {#if agents.length === 0}
        <p class="empty">No agents registered</p>
      {:else}
        <div class="agent-list">
          {#each agents as agent}
            <article>
              <header>
                <strong>{agent.name}</strong>
                <span class="role-badge role-{agent.status}">{agent.status}</span>
              </header>
              {#if agent.status === 'pending'}
                <button
                  type="button"
                  on:click={() => approveAgent(agent)}
                  disabled={loading}
                >
                  Approve
                </button>
              {:else if agent.status === 'approved'}
                <button
                  type="button"
                  on:click={() => viewAgentFiles(agent)}
                  disabled={loading}
                  title="Browse the files this agent has indexed"
                >
                  {openAgentId === agent.id ? 'Hide files' : 'View files'}
                </button>
              {/if}

              {#if openAgentId === agent.id}
                {#if agentFiles.length === 0}
                  <p class="empty">No files reported yet. Run <code>paracord-agent sync</code> on the workstation.</p>
                {:else}
                  <ul class="agent-files">
                    {#each agentFiles as file (file.id)}
                      <li>
                        <span class="fname">{file.display_path ?? file.local_file_id.slice(0, 10)}</span>
                        {#if file.teleport_status === 'complete'}
                          <span class="role-badge role-approved">in library</span>
                        {:else if file.teleport_status === 'requested'}
                          <span class="role-badge role-pending">awaiting push</span>
                        {:else}
                          <button type="button" on:click={() => teleport(agent.id, file)} disabled={loading}
                            title="Request this file be teleported into the library">Teleport</button>
                        {/if}
                      </li>
                    {/each}
                  </ul>
                {/if}
              {/if}
            </article>
          {/each}
        </div>
      {/if}
    </section>

    <!-- Audit Events -->
    <section class="surface admin-section audit-section">
      <div class="section-head">
        <h2>Audit Events</h2>
        <span>{auditEvents.length}</span>
      </div>

      {#if auditEvents.length === 0}
        <p class="empty">No audit events</p>
      {:else}
        <div class="audit-list">
          {#each auditEvents as event}
            <article>
              <header>
                <strong>{event.event_type}</strong>
                <small>{formatDate(event.created_at)}</small>
              </header>
              {#if event.entity_type}
                <span>{event.entity_type}{event.entity_id ? ` · ${event.entity_id.slice(0, 8)}` : ''}</span>
              {/if}
            </article>
          {/each}
        </div>
      {/if}
    </section>
  </div>
</div>

<style>
  .admin-layout {
    max-width: 92rem;
    margin: 0 auto;
  }

  .admin-columns {
    display: grid;
    grid-template-columns: 1fr 1fr 1.5fr;
    gap: 1rem;
    align-items: start;
  }

  @media (max-width: 900px) {
    .admin-columns {
      grid-template-columns: 1fr;
    }
  }

  .admin-section {
    padding: 1rem;
    background: white;
    border-radius: 0.5rem;
  }

  .surface {
    background: white;
    border-radius: 0.5rem;
    padding: 1rem;
  }

  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.75rem;
  }

  .section-head h2 {
    margin: 0;
    font-size: 1rem;
    font-weight: 600;
  }

  h2 {
    font-size: 1rem;
    font-weight: 600;
    margin: 0 0 0.75rem;
  }

  .stack {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }

  .inline-action {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
    flex-wrap: wrap;
  }

  input, select {
    padding: 0.4rem 0.6rem;
    border: 1px solid #cbd5e1;
    border-radius: 0.375rem;
    font-size: 0.85rem;
    width: 100%;
  }

  /* Light "secondary"-style buttons; the explicit color avoids inheriting the global
     primary's white text (which previously rendered white-on-white here). */
  button {
    padding: 0.4rem 0.75rem;
    border: 1px solid var(--pg-border, #cbd5e1);
    border-radius: 0.375rem;
    background: var(--pg-secondary-bg, #ffffff);
    color: var(--pg-secondary-text, #21303d);
    font-size: 0.85rem;
    cursor: pointer;
    white-space: nowrap;
  }

  button:hover:not(:disabled) {
    background: var(--pg-secondary-hover, #eef2f6);
  }

  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .user-list, .agent-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  article {
    border: 1px solid #e2e8f0;
    border-radius: 0.375rem;
    padding: 0.6rem 0.75rem;
  }

  article.disabled {
    opacity: 0.6;
  }

  header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.4rem;
    flex-wrap: wrap;
  }

  .role-badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.4rem;
    border-radius: 0.25rem;
    background: #e2e8f0;
    font-weight: 600;
  }

  .role-owner { background: #fde68a; color: #78350f; }
  .role-editor { background: #bfdbfe; color: #1e3a5f; }
  .role-approved { background: #bbf7d0; color: #14532d; }
  .role-pending { background: #fef9c3; color: #713f12; }

  .disabled-badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.4rem;
    border-radius: 0.25rem;
    background: #fecaca;
    color: #7f1d1d;
    font-weight: 600;
  }

  .user-actions {
    display: flex;
    gap: 0.4rem;
    margin-bottom: 0.25rem;
    align-items: center;
  }

  .user-actions select {
    width: auto;
    flex: 1;
  }

  .token-box {
    margin: 0.75rem 0;
    padding: 0.6rem;
    background: #f0fdf4;
    border: 1px solid #86efac;
    border-radius: 0.375rem;
    font-size: 0.8rem;
  }

  .token-box code {
    display: block;
    word-break: break-all;
    font-family: monospace;
    margin: 0.25rem 0;
    font-size: 0.75rem;
  }

  .audit-list {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    max-height: 600px;
    overflow-y: auto;
  }

  .audit-list article {
    padding: 0.4rem 0.6rem;
  }

  .audit-list header {
    margin-bottom: 0.15rem;
  }

  .empty {
    color: #94a3b8;
    font-size: 0.85rem;
    font-style: italic;
    margin: 0.5rem 0;
  }

  .message {
    padding: 0.5rem 0.75rem;
    background: #f0f9ff;
    border: 1px solid #bae6fd;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    margin-bottom: 0.75rem;
  }

  small {
    font-size: 0.75rem;
    color: #64748b;
  }

  .small-help {
    font-size: 0.8rem;
    line-height: 1.45;
    margin: 0 0 0.6rem;
  }

  .agent-files {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
  }

  .agent-files li {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
  }

  .agent-files .fname {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .agent-files button {
    min-height: 1.8rem;
    padding: 0.15rem 0.5rem;
  }
</style>
