<script lang="ts">
  import {
    ApiClient,
    type AccessLevel,
    type AdminUser,
    type AgentFileRecord,
    type AgentPrivilege,
    type AgentRecord,
    type DefaultGrant,
    type Grant,
    type GrantTargetType,
    type Group,
    type GroupMember,
    type Rack,
    type ServerImportRoot,
    type Shelf,
    type UserRole,
    type WebFindAllowedHost,
    type WebFindDownloadPolicy,
  } from '../api/client';
  import { get } from 'svelte/store';

  import AiModelsPanel from '../components/AiModelsPanel.svelte';
  import Modal from '../components/Modal.svelte';
  import { canManageUsers, currentUser, isOwner } from '../lib/session';

  // Static reference shown under the Users widget so an admin/owner can understand each role
  // before assigning it. Kept in sync with ProfilePage's own-role description.
  const ROLE_GUIDE: { role: UserRole; label: string; blurb: string }[] = [
    { role: 'reader', label: 'Reader', blurb: 'Browse, search and read papers; cannot modify the library.' },
    {
      role: 'contributor',
      label: 'Contributor',
      blurb: 'Everything a reader can do, plus import, edit, enrich and delete their OWN papers (papers they created).',
    },
    { role: 'editor', label: 'Editor', blurb: 'Everything a contributor can do, but may edit and delete any paper they can see (not just their own).' },
    {
      role: 'librarian',
      label: 'Librarian',
      blurb: 'Everything an editor can do, plus create, edit and organise racks and shelves and manage their membership and access.',
    },
    {
      role: 'admin',
      label: 'Admin',
      blurb: 'Everything a librarian can do, plus manage users (reader/contributor/editor/librarian), groups, agents, AI settings and the audit log. Cannot manage other admins or the owner.',
    },
    {
      role: 'owner',
      label: 'Owner',
      blurb: 'The single, permanent account. Everything an admin can do, plus manage admins. Cannot be disabled, deleted or role-changed.',
    },
  ];

  // Roles an admin/owner may assign. The owner role is never assignable (it is the immutable
  // bootstrap account); the admin role is offered to the owner only.
  $: assignableRoles = ($isOwner
    ? (['reader', 'contributor', 'editor', 'librarian', 'admin'] as UserRole[])
    : (['reader', 'contributor', 'editor', 'librarian'] as UserRole[]));

  // The signed-in user's id, for blocking self-disable / self-delete in the UI (the server also
  // enforces this).
  $: meId = $currentUser?.id ?? null;

  /** The owner row is fully locked: no role-change, disable, delete or password reset. */
  function isOwnerRow(user: AdminUser): boolean {
    return user.role === 'owner' || user.is_bootstrap;
  }

  /** Admin rows may only be managed by the owner; everyone-else rows are managed by any admin. */
  function canManageRow(user: AdminUser): boolean {
    if (isOwnerRow(user)) return false;
    if (user.role === 'admin') return $isOwner;
    return true;
  }

  const PRIVILEGES: { key: AgentPrivilege; label: string; hint: string }[] = [
    { key: 'can_index', label: 'index', hint: 'Accept manifests (file listings) from this agent.' },
    { key: 'can_extract', label: 'extract', hint: 'Accept upload-for-extraction (PDF discarded after, preview kept).' },
    { key: 'can_teleport', label: 'teleport', hint: 'Accept permanent file uploads into the library.' },
    { key: 'can_be_requested', label: 'be requested', hint: 'Allow teleport requests to this agent.' },
    { key: 'processing_visibility', label: 'see processing', hint: 'Agent may see extraction/processing status of its files.' },
    { key: 'server_status_visibility', label: 'see server', hint: 'Agent may see server up/health status.' },
  ];

  export let client: ApiClient;

  let users: AdminUser[] = [];
  let agents: AgentRecord[] = [];
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

  // Server import roots (owner-only): the merged yaml-fixed + DB-managed whitelist (batch 2 #19).
  let importRoots: ServerImportRoot[] = [];
  let newRootAlias = '';
  let newRootPath = '';

  // Find-on-web allowed download hosts (owner+admin): merged built-in defaults + DB rows (batch 2 #5).
  let allowedHosts: WebFindAllowedHost[] = [];
  let newAllowedHost = '';

  // Find-on-web download policy (owner-only): restricted | careful | unrestricted (find-on-web v2).
  const DOWNLOAD_POLICIES: { value: WebFindDownloadPolicy; label: string; blurb: string }[] = [
    {
      value: 'restricted',
      label: 'Restricted',
      blurb: 'Allow-list only — downloads must resolve to a host on the allow-list above.',
    },
    {
      value: 'careful',
      label: 'Careful',
      blurb: 'Allow-list plus well-known publishers and open-access hosts.',
    },
    {
      value: 'unrestricted',
      label: 'Unrestricted',
      blurb:
        'Any host, with a per-download confirmation for unknown hosts. Shadow libraries and internal hosts are always blocked.',
    },
  ];
  let downloadPolicy: WebFindDownloadPolicy | null = null;

  // --- Access-control groups (admin-or-owner; Phase H) ---
  const ACCESS_LEVELS: { value: AccessLevel; label: string }[] = [
    { value: 'open', label: 'Open — visible to everyone by default' },
    { value: 'visible', label: 'Visible — listed to everyone; modify needs a grant' },
    { value: 'private', label: 'Private — only granted groups may see it' },
  ];

  let groups: Group[] = [];
  let racks: Rack[] = [];
  let shelves: Shelf[] = [];
  let newGroupName = '';

  // The currently-expanded group + its loaded members and grants.
  let openGroupId = '';
  let groupMembers: GroupMember[] = [];
  let groupGrants: Grant[] = [];
  let pickMemberId = '';
  let grantTargetType: GrantTargetType = 'shelf';
  let pickGrantTargetId = '';

  // Defaults subsection: default access level + the default-grant list.
  let defaultGrants: DefaultGrant[] = [];
  let defaultAccessLevel: AccessLevel | null = null;
  let defaultGrantType: GrantTargetType = 'shelf';
  let pickDefaultTargetId = '';

  // Target label lookup for grant rows (rack/shelf name from the loaded lists).
  function targetLabel(type: GrantTargetType, id: string): string {
    const list = type === 'rack' ? racks : shelves;
    return list.find((t) => t.id === id)?.name ?? `${type} ${id.slice(0, 8)}`;
  }

  // Users not already in the open group, offered in the "add member" select.
  $: nonMemberUsers = users.filter((u) => !groupMembers.some((m) => m.id === u.id));

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
      [users, agents] = await Promise.all([
        client.listAdminUsers(),
        client.listAgents(),
      ]);
      // The import-roots whitelist is owner-only (the endpoint 403s for admins).
      if (get(isOwner)) importRoots = await client.listServerImportRoots();
      // The find-on-web allowed-hosts list is owner+admin.
      if (get(canManageUsers)) allowedHosts = await client.listWebFindAllowedHosts();
      // The download policy is owner-only (the endpoint 403s for admins).
      if (get(isOwner)) downloadPolicy = (await client.getWebFindDownloadPolicy()).policy;
      // Groups, racks/shelves (for grant pickers) and default settings are admin-or-owner.
      if (get(canManageUsers)) {
        [groups, racks, shelves, defaultGrants] = await Promise.all([
          client.listGroups(),
          client.listRacks(),
          client.listShelves(),
          client.listDefaultGrants(),
        ]);
        defaultAccessLevel = (await client.getAccessSettings()).default_access_level;
      }
    });
  }

  // --- Groups ---
  async function createGroup(): Promise<void> {
    const name = newGroupName.trim();
    if (!name) return;
    await run(async () => {
      await client.createGroup(name);
      newGroupName = '';
      groups = await client.listGroups();
    }, 'Group created');
  }

  async function deleteGroup(group: Group): Promise<void> {
    if (group.is_personal) return;
    if (!window.confirm(`Delete group “${group.name}”? Its members and access grants are removed.`)) return;
    await run(async () => {
      await client.deleteGroup(group.id);
      if (openGroupId === group.id) closeGroup();
      groups = await client.listGroups();
    }, 'Group deleted');
  }

  function closeGroup(): void {
    openGroupId = '';
    groupMembers = [];
    groupGrants = [];
    pickMemberId = '';
    pickGrantTargetId = '';
  }

  async function openGroup(group: Group): Promise<void> {
    if (openGroupId === group.id) {
      closeGroup();
      return;
    }
    await run(async () => {
      [groupMembers, groupGrants] = await Promise.all([
        client.listGroupMembers(group.id),
        client.listGroupGrants(group.id),
      ]);
      openGroupId = group.id;
      pickMemberId = '';
      pickGrantTargetId = '';
    });
  }

  async function addMember(): Promise<void> {
    if (!openGroupId || !pickMemberId) return;
    const groupId = openGroupId;
    await run(async () => {
      await client.addGroupMember(groupId, pickMemberId);
      pickMemberId = '';
      groupMembers = await client.listGroupMembers(groupId);
    }, 'Member added');
  }

  async function removeMember(member: GroupMember): Promise<void> {
    if (!openGroupId) return;
    const groupId = openGroupId;
    await run(async () => {
      await client.removeGroupMember(groupId, member.id);
      groupMembers = await client.listGroupMembers(groupId);
    }, 'Member removed');
  }

  async function addGrant(): Promise<void> {
    if (!openGroupId || !pickGrantTargetId) return;
    const groupId = openGroupId;
    await run(async () => {
      await client.addGroupGrant(groupId, grantTargetType, pickGrantTargetId);
      pickGrantTargetId = '';
      groupGrants = await client.listGroupGrants(groupId);
    }, 'Grant added');
  }

  async function removeGrant(grant: Grant): Promise<void> {
    if (!openGroupId) return;
    const groupId = openGroupId;
    await run(async () => {
      await client.removeGrant(grant.id);
      groupGrants = await client.listGroupGrants(groupId);
    }, 'Grant removed');
  }

  // --- Defaults ---
  async function changeDefaultAccess(level: AccessLevel): Promise<void> {
    if (level === defaultAccessLevel) return;
    await run(async () => {
      defaultAccessLevel = (await client.setAccessSettings(level)).default_access_level;
    }, 'Default access level updated');
  }

  async function addDefaultGrant(): Promise<void> {
    if (!pickDefaultTargetId) return;
    await run(async () => {
      await client.addDefaultGrant(defaultGrantType, pickDefaultTargetId);
      pickDefaultTargetId = '';
      defaultGrants = await client.listDefaultGrants();
    }, 'Default grant added');
  }

  async function removeDefaultGrant(grant: DefaultGrant): Promise<void> {
    await run(async () => {
      await client.removeDefaultGrant(grant.id);
      defaultGrants = await client.listDefaultGrants();
    }, 'Default grant removed');
  }

  async function changeDownloadPolicy(policy: WebFindDownloadPolicy): Promise<void> {
    if (policy === downloadPolicy) return;
    await run(async () => {
      downloadPolicy = (await client.setWebFindDownloadPolicy(policy)).policy;
    }, 'Download policy updated');
  }

  async function addAllowedHost(): Promise<void> {
    const host = newAllowedHost.trim();
    if (!host) return;
    await run(async () => {
      await client.addWebFindAllowedHost({ host });
      newAllowedHost = '';
      allowedHosts = await client.listWebFindAllowedHosts();
    }, 'Allowed host added');
  }

  async function removeAllowedHost(entry: WebFindAllowedHost): Promise<void> {
    if (!entry.removable || !entry.id) return;
    if (!window.confirm(`Remove “${entry.host}” from the find-on-web allowed-downloads list? New paper downloads can no longer be fetched from this host.`))
      return;
    await run(async () => {
      await client.removeWebFindAllowedHost(entry.id as string);
      allowedHosts = await client.listWebFindAllowedHosts();
    }, 'Allowed host removed');
  }

  async function addImportRoot(): Promise<void> {
    const alias = newRootAlias.trim();
    const path = newRootPath.trim();
    if (!alias || !path) return;
    await run(async () => {
      await client.addServerImportRoot({ alias, path });
      newRootAlias = '';
      newRootPath = '';
      importRoots = await client.listServerImportRoots();
    }, 'Import root added');
  }

  async function removeImportRoot(root: ServerImportRoot): Promise<void> {
    if (!root.removable || !root.id) return;
    if (!window.confirm(`Remove import root “${root.alias}” (${root.path})? Existing imports stay; new server-folder imports can no longer use this alias.`))
      return;
    await run(async () => {
      await client.removeServerImportRoot(root.id as string);
      importRoots = await client.listServerImportRoots();
    }, 'Import root removed');
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

  async function removeUser(user: AdminUser): Promise<void> {
    if (
      !window.confirm(
        `Permanently delete “${user.username}”? This cannot be undone (the account is already disabled).`,
      )
    )
      return;
    await run(async () => {
      await client.deleteUser(user.id);
      users = await client.listAdminUsers();
    }, 'User deleted');
  }

  async function enableUser(user: AdminUser): Promise<void> {
    await run(async () => {
      await client.enableUser(user.id);
      users = await client.listAdminUsers();
    }, 'User re-enabled');
  }

  // --- Reset another user's password ---
  let resetTarget: AdminUser | null = null;
  let resetPw = '';
  let resetMsg = '';

  function openReset(user: AdminUser): void {
    resetTarget = user;
    resetPw = '';
    resetMsg = '';
  }

  function closeReset(): void {
    resetTarget = null;
    resetPw = '';
    resetMsg = '';
  }

  async function submitReset(): Promise<void> {
    const target = resetTarget;
    if (!target) return;
    resetMsg = '';
    await run(async () => {
      const result = await client.resetUserPassword(target.id, resetPw);
      resetMsg = `Password reset (${result.sessions_revoked} session(s) signed out).`;
      resetPw = '';
    });
    // run() set `message` on failure; keep the dialog open so the owner can retry.
    if (!message) closeReset();
  }

  async function issueEnrollToken(): Promise<void> {
    await run(async () => {
      const result = await client.issueEnrollToken();
      enrollToken = result.token;
      enrollExpiry = result.expires_at;
    }, 'Enrollment token issued — copy it now, it will not be shown again');
  }

  async function togglePrivilege(agent: AgentRecord, key: AgentPrivilege, value: boolean): Promise<void> {
    await run(async () => {
      await client.updateAgentPrivileges(agent.id, { [key]: value });
      agents = await client.listAgents();
    }, 'Privileges updated');
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

  async function renameAgent(agent: AgentRecord): Promise<void> {
    const name = window.prompt('Rename agent', agent.name);
    if (name === null || name.trim() === '' || name.trim() === agent.name) return;
    await run(async () => {
      await client.renameAgent(agent.id, name.trim());
      agents = await client.listAgents();
    }, 'Agent renamed');
  }

  async function removeAgent(agent: AgentRecord): Promise<void> {
    if (
      !window.confirm(
        `Remove agent “${agent.name}”? Its token is revoked and its indexed-file records are deleted. ` +
          'Files it already teleported or extracted stay in the library.',
      )
    )
      return;
    await run(async () => {
      await client.deleteAgent(agent.id);
      if (openAgentId === agent.id) {
        openAgentId = '';
        agentFiles = [];
      }
      agents = await client.listAgents();
    }, 'Agent removed');
  }

  // Load whenever the authenticated client is (re)created — including the null-token → authed
  // transition on a hard refresh, which a dependency-less `$: void refresh()` would miss (it ran
  // once before the token was read, leaving the page empty until the tab was clicked again).
  let loadedFor: ApiClient | null = null;
  $: if (client && client !== loadedFor) {
    loadedFor = client;
    void refresh();
  }

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
        <button type="button" on:click={refresh} disabled={loading} title="Reload the users and agents lists">Refresh</button>
      </div>

      <form on:submit|preventDefault={createUser} class="stack">
        <input bind:value={newUsername} placeholder="Username" autocomplete="off" />
        <input bind:value={newPassword} type="password" placeholder="Password" autocomplete="new-password" />
        <select bind:value={newRole} title={$isOwner ? 'Role for the new account' : 'Role for the new account (admins can create readers and editors)'}>
          {#each assignableRoles as r}
            <option value={r}>{r}</option>
          {/each}
        </select>
        <button type="submit" disabled={!newUsername || !newPassword || loading}
          title={newUsername && newPassword ? 'Create the new account' : 'Enter a username and password first'}>Create user</button>
      </form>

      {#if users.length === 0}
        <p class="empty">No users loaded</p>
      {:else}
        <div class="user-list">
          {#each users as user}
            {@const owned = isOwnerRow(user)}
            {@const manageable = canManageRow(user)}
            {@const isSelf = user.id === meId}
            <article class:disabled={!!user.disabled_at} class:locked={owned}>
              <header>
                <strong>{user.username}</strong>
                <span class="role-badge role-{user.role}">{user.role}</span>
                {#if owned}
                  <span class="lock-badge" title="The owner account is permanent and cannot be changed">owner · locked</span>
                {:else if isSelf}
                  <span class="self-badge" title="This is you">you</span>
                {/if}
                {#if user.disabled_at}
                  <span class="disabled-badge">disabled</span>
                {/if}
              </header>
              {#if owned}
                <p class="muted small-help">
                  The owner is the single permanent account and cannot be disabled, deleted or
                  role-changed.
                </p>
              {:else if !manageable}
                <p class="muted small-help">
                  Only the owner can manage administrator accounts.
                </p>
              {:else}
                <div class="user-actions">
                  <select
                    value={user.role}
                    on:change={(e) => changeRole(user, e.currentTarget.value as UserRole)}
                    disabled={loading || !!user.disabled_at}
                    title={$isOwner ? 'Change this user’s role' : 'Admins can set reader or editor; only the owner grants admin'}
                  >
                    {#each assignableRoles as r}
                      <option value={r}>{r}</option>
                    {/each}
                  </select>
                  <button
                    type="button"
                    on:click={() => openReset(user)}
                    disabled={loading}
                    title="Set a new password for this user (signs out their sessions)"
                  >
                    Reset password
                  </button>
                  {#if user.disabled_at}
                    <button
                      type="button"
                      on:click={() => enableUser(user)}
                      disabled={loading}
                      title="Re-enable this account"
                    >
                      Re-enable
                    </button>
                    <button
                      type="button"
                      class="link-btn danger"
                      on:click={() => removeUser(user)}
                      disabled={loading || isSelf}
                      title={isSelf ? 'You cannot delete your own account' : 'Permanently delete this disabled account'}
                    >
                      Delete
                    </button>
                  {:else}
                    <button
                      type="button"
                      on:click={() => disableUser(user)}
                      disabled={loading || isSelf}
                      title={isSelf ? 'You cannot disable your own account' : 'Disable this account (sign-in blocked; can be re-enabled)'}
                    >
                      Disable
                    </button>
                  {/if}
                </div>
              {/if}
              <small>Created {formatDate(user.created_at)}</small>
            </article>
          {/each}
        </div>
      {/if}

      <div class="role-guide">
        <h3>What each role can do</h3>
        {#each ROLE_GUIDE as r}
          <div class="role-guide-item">
            <span class="role-badge role-{r.role}">{r.label}</span>
            <span>{r.blurb}</span>
          </div>
        {/each}
      </div>
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
        <button type="button" on:click={issueEnrollToken} disabled={loading}
          title="Mint a one-time token to enroll a new workstation agent">
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
                <span class="agent-actions">
                  <button
                    type="button"
                    class="link-btn"
                    on:click={() => renameAgent(agent)}
                    disabled={loading}
                    title="Rename this agent"
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    class="link-btn danger"
                    on:click={() => removeAgent(agent)}
                    disabled={loading}
                    title="Remove this agent and revoke its token"
                  >
                    Remove
                  </button>
                </span>
              </header>
              {#if agent.status === 'pending'}
                <button
                  type="button"
                  on:click={() => approveAgent(agent)}
                  disabled={loading}
                  title="Approve this agent and mint its bearer token"
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

              {#if agent.status === 'approved'}
                <div class="privileges" role="group" aria-label="Agent privileges">
                  {#each PRIVILEGES as priv (priv.key)}
                    <label class="priv" title={priv.hint}>
                      <input
                        type="checkbox"
                        checked={agent[priv.key]}
                        on:change={(e) => togglePrivilege(agent, priv.key, e.currentTarget.checked)}
                        disabled={loading}
                      />
                      {priv.label}
                    </label>
                  {/each}
                </div>
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

    <!-- AI & Models -->
    <div class="surface admin-section">
      <AiModelsPanel {client} />
    </div>
  </div>

  <!-- Server import folders (owner-only; batch 2 #19) -->
  {#if $isOwner}
    <section class="surface admin-section import-roots">
      <h2>Server import folders</h2>
      <p class="muted">
        Whitelisted folders <strong>on the server machine</strong> that the “Server folder” import
        may scan. The locked entries come from <code>storage.server_allowed_roots</code> in
        <code>server.yaml</code> and can only be changed there; the entries you add below are stored
        in the database and merged with them. <strong>Papers on your own computer?</strong> Use a
        local agent instead — server-folder can’t reach your PC.
      </p>

      <table class="roots">
        <thead>
          <tr><th>Alias</th><th>Path</th><th>Source</th><th></th></tr>
        </thead>
        <tbody>
          {#each importRoots as root (root.alias)}
            <tr>
              <td><code>{root.alias}</code></td>
              <td class="path"><code>{root.path}</code>
                {#if !root.exists}<span class="warn" title="This folder does not currently exist on the server">missing</span>{/if}
              </td>
              <td>
                {#if root.source === 'yaml'}
                  <span class="lock-badge" title="Defined in server.yaml — edit that file to change it">yaml · locked</span>
                {:else}
                  <span class="db-badge" title="Added here and stored in the database">database</span>
                {/if}
              </td>
              <td>
                {#if root.removable}
                  <button type="button" class="danger" on:click={() => removeImportRoot(root)}
                    disabled={loading}
                    title="Remove this database-managed import root">Remove</button>
                {:else}
                  <button type="button" class="danger" disabled
                    title="Locked: defined in server.yaml. Edit that file to remove it.">Remove</button>
                {/if}
              </td>
            </tr>
          {/each}
          {#if importRoots.length === 0}
            <tr><td colspan="4" class="muted">No import folders yet. Add one below or define them in <code>server.yaml</code>.</td></tr>
          {/if}
        </tbody>
      </table>

      <form class="add-root" on:submit|preventDefault={addImportRoot}>
        <input bind:value={newRootAlias} placeholder="Alias (e.g. shared-drive)" aria-label="Import root alias" />
        <input bind:value={newRootPath} placeholder="Absolute path on the server (e.g. /data/papers)" aria-label="Import root path" />
        <button type="submit" disabled={loading || !newRootAlias.trim() || !newRootPath.trim()}
          title={newRootAlias.trim() && newRootPath.trim()
            ? 'Add this server folder to the import whitelist (path must exist and be a directory)'
            : 'Enter both an alias and an absolute server path first'}>Add folder</button>
      </form>
      <p class="hintline">The path must already exist as a directory on the server, and the alias must be unique across all roots.</p>
    </section>
  {/if}

  <!-- Find-on-web allowed download hosts (owner+admin; batch 2 #5) -->
  {#if $canManageUsers}
    <section class="surface admin-section allowed-hosts">
      <h2>Find-on-web allowed hosts</h2>
      <p class="muted">
        When you fetch a paper PDF with <strong>Find on web</strong>, the server only downloads it
        if the final host is on this allowlist. The locked entries are built-in, well-known
        open-access hosts and cannot be removed; the entries you add below are stored in the
        database and merged with them. Known shadow libraries are always refused, even if added here.
      </p>

      <table class="hosts">
        <thead>
          <tr><th>Host</th><th>Source</th><th></th></tr>
        </thead>
        <tbody>
          {#each allowedHosts as entry (entry.host)}
            <tr>
              <td><code>{entry.host}</code></td>
              <td>
                {#if entry.source === 'default'}
                  <span class="lock-badge" title="Built-in default host — cannot be removed">default · locked</span>
                {:else}
                  <span class="db-badge" title="Added here and stored in the database">database</span>
                {/if}
              </td>
              <td>
                {#if entry.removable}
                  <button type="button" class="danger" on:click={() => removeAllowedHost(entry)}
                    disabled={loading}
                    title="Remove this database-managed allowed host">Remove</button>
                {:else}
                  <button type="button" class="danger" disabled
                    title="Locked: built-in default host.">Remove</button>
                {/if}
              </td>
            </tr>
          {/each}
          {#if allowedHosts.length === 0}
            <tr><td colspan="3" class="muted">No hosts yet.</td></tr>
          {/if}
        </tbody>
      </table>

      <form class="add-host" on:submit|preventDefault={addAllowedHost}>
        <input bind:value={newAllowedHost} placeholder="Host (e.g. repository.example.org or *.example.org)" aria-label="Allowed host" />
        <button type="submit" disabled={loading || !newAllowedHost.trim()}
          title={newAllowedHost.trim()
            ? 'Add this host to the find-on-web allowed-downloads list'
            : 'Enter a hostname first'}>Add host</button>
      </form>
      <p class="hintline">A bare host (e.g. <code>example.org</code>) also covers its subdomains; use the <code>*.example.org</code> form to allow subdomains only.</p>
    </section>
  {/if}

  <!-- Find-on-web download policy (owner-only; find-on-web v2). -->
  {#if $isOwner}
    <section class="surface admin-section download-policy">
      <h2>Find-on-web download policy</h2>
      <p class="muted">
        Controls which hosts the server will fetch paper PDFs from during <strong>Find on web</strong>.
        Stricter policies download less automatically; <strong>Unrestricted</strong> asks for a
        per-download confirmation before fetching from an unknown host. Shadow libraries and internal
        hosts are always blocked, whatever the policy.
      </p>
      <fieldset class="policy-options">
        <legend class="sr-only">Download policy</legend>
        {#each DOWNLOAD_POLICIES as opt (opt.value)}
          <label class="policy-option" class:active={downloadPolicy === opt.value}>
            <input
              type="radio"
              name="download-policy"
              value={opt.value}
              checked={downloadPolicy === opt.value}
              on:change={() => changeDownloadPolicy(opt.value)}
              disabled={loading || downloadPolicy === null}
              title={downloadPolicy === null
                ? 'Loading the current policy…'
                : `Set the download policy to “${opt.label}”`}
            />
            <span class="policy-text">
              <strong>{opt.label}</strong>
              <small class="muted">{opt.blurb}</small>
            </span>
          </label>
        {/each}
      </fieldset>
    </section>
  {/if}

  <!-- Access-control groups (admin-or-owner; Phase H) -->
  {#if $canManageUsers}
    <section class="surface admin-section groups">
      <h2>Groups &amp; access</h2>
      <p class="muted">
        Groups gate who can <strong>see</strong> and <strong>modify</strong> private/visible racks and
        shelves. Each user has a permanent <strong>personal group</strong> (named after them); create
        shared groups to grant several users access at once. Grant a group access to a rack or shelf
        below, then add the users who should have it.
      </p>

      <form class="add-group" on:submit|preventDefault={createGroup}>
        <input bind:value={newGroupName} placeholder="New group name (e.g. nlp-team)" aria-label="New group name" />
        <button type="submit" disabled={loading || !newGroupName.trim()}
          title={newGroupName.trim() ? 'Create a shared group' : 'Enter a group name first'}>Create group</button>
      </form>

      {#if groups.length === 0}
        <p class="empty">No groups loaded</p>
      {:else}
        <div class="group-list">
          {#each groups as group (group.id)}
            <article>
              <header>
                <strong>{group.name}</strong>
                {#if group.is_personal}
                  <span class="lock-badge" title="Each user has one permanent personal group; it cannot be deleted">personal</span>
                {/if}
                <span class="group-actions">
                  <button type="button" class="link-btn" on:click={() => openGroup(group)} disabled={loading}
                    title="Manage this group's members and access grants">
                    {openGroupId === group.id ? 'Hide' : 'Manage'}
                  </button>
                  {#if group.is_personal}
                    <button type="button" class="link-btn danger" disabled
                      title="Personal groups cannot be deleted">Delete</button>
                  {:else}
                    <button type="button" class="link-btn danger" on:click={() => deleteGroup(group)} disabled={loading}
                      title="Delete this group (members and grants are removed)">Delete</button>
                  {/if}
                </span>
              </header>

              {#if openGroupId === group.id}
                <div class="group-detail">
                  <!-- Members -->
                  <div class="subgroup">
                    <h4>Members ({groupMembers.length})</h4>
                    <div class="picker">
                      <select bind:value={pickMemberId} aria-label="Choose a user to add" title="Choose a user to add to this group">
                        <option value="">Choose a user…</option>
                        {#each nonMemberUsers as u (u.id)}<option value={u.id}>{u.username}</option>{/each}
                      </select>
                      <button type="button" on:click={addMember} disabled={loading || !pickMemberId}
                        title={pickMemberId ? 'Add the chosen user' : 'Choose a user first'}>Add member</button>
                    </div>
                    {#if groupMembers.length === 0}
                      <p class="empty">No members yet.</p>
                    {:else}
                      <ul class="row-list">
                        {#each groupMembers as member (member.id)}
                          <li>
                            <span>{member.username} <span class="role-badge role-{member.role}">{member.role}</span></span>
                            <button type="button" class="link-btn danger" on:click={() => removeMember(member)} disabled={loading}
                              title="Remove this user from the group">Remove</button>
                          </li>
                        {/each}
                      </ul>
                    {/if}
                  </div>

                  <!-- Grants -->
                  <div class="subgroup">
                    <h4>Access grants ({groupGrants.length})</h4>
                    <div class="picker">
                      <select bind:value={grantTargetType} aria-label="Grant target type" title="Grant access to a rack or a shelf">
                        <option value="shelf">Shelf</option>
                        <option value="rack">Rack</option>
                      </select>
                      <select bind:value={pickGrantTargetId} aria-label="Choose a rack or shelf" title="Choose the rack or shelf to grant">
                        <option value="">Choose a {grantTargetType}…</option>
                        {#each (grantTargetType === 'rack' ? racks : shelves) as t (t.id)}<option value={t.id}>{t.name}</option>{/each}
                      </select>
                      <button type="button" on:click={addGrant} disabled={loading || !pickGrantTargetId}
                        title={pickGrantTargetId ? 'Grant this group access' : 'Choose a target first'}>Add grant</button>
                    </div>
                    {#if groupGrants.length === 0}
                      <p class="empty">No grants yet.</p>
                    {:else}
                      <ul class="row-list">
                        {#each groupGrants as grant (grant.id)}
                          <li>
                            <span><span class="type-badge">{grant.target_type}</span> {targetLabel(grant.target_type, grant.target_id)}</span>
                            <button type="button" class="link-btn danger" on:click={() => removeGrant(grant)} disabled={loading}
                              title="Revoke this access grant">Remove</button>
                          </li>
                        {/each}
                      </ul>
                    {/if}
                  </div>
                </div>
              {/if}
            </article>
          {/each}
        </div>
      {/if}

      <!-- Defaults -->
      <div class="defaults">
        <h3>Defaults</h3>
        <p class="muted small-help">
          The <strong>default access level</strong> applies to new racks and shelves that don't set
          their own. <strong>Default grants</strong> are applied to every new user's personal group,
          so new users get access to these racks/shelves automatically.
        </p>

        <label class="access-inline">
          <span>Default access level</span>
          <select
            value={defaultAccessLevel}
            on:change={(e) => changeDefaultAccess(e.currentTarget.value as AccessLevel)}
            disabled={loading || defaultAccessLevel === null}
            aria-label="Default access level"
            title={defaultAccessLevel === null ? 'Loading…' : 'Access level for new racks and shelves'}
          >
            {#each ACCESS_LEVELS as lvl}<option value={lvl.value}>{lvl.label}</option>{/each}
          </select>
        </label>

        <h4>Default grants ({defaultGrants.length})</h4>
        <div class="picker">
          <select bind:value={defaultGrantType} aria-label="Default grant target type" title="A rack or a shelf">
            <option value="shelf">Shelf</option>
            <option value="rack">Rack</option>
          </select>
          <select bind:value={pickDefaultTargetId} aria-label="Choose a rack or shelf" title="Choose the rack or shelf to grant to new users">
            <option value="">Choose a {defaultGrantType}…</option>
            {#each (defaultGrantType === 'rack' ? racks : shelves) as t (t.id)}<option value={t.id}>{t.name}</option>{/each}
          </select>
          <button type="button" on:click={addDefaultGrant} disabled={loading || !pickDefaultTargetId}
            title={pickDefaultTargetId ? 'Add this default grant' : 'Choose a target first'}>Add default grant</button>
        </div>
        {#if defaultGrants.length === 0}
          <p class="empty">No default grants — new users only get their personal group.</p>
        {:else}
          <ul class="row-list">
            {#each defaultGrants as grant (grant.id)}
              <li>
                <span><span class="type-badge">{grant.target_type}</span> {targetLabel(grant.target_type, grant.target_id)}</span>
                <button type="button" class="link-btn danger" on:click={() => removeDefaultGrant(grant)} disabled={loading}
                  title="Remove this default grant (existing users keep their access)">Remove</button>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    </section>
  {/if}
</div>

{#if resetTarget}
  <Modal title={`Reset password — ${resetTarget.username}`} onClose={closeReset}>
    <form class="stack" on:submit|preventDefault={submitReset}>
      <p class="muted">
        Set a new password for <strong>{resetTarget.username}</strong>. This signs out all of their
        active sessions. Share the new password with them over a trusted channel.
      </p>
      <input
        type="password"
        bind:value={resetPw}
        placeholder="New password (min 8 characters)"
        autocomplete="new-password"
      />
      <div class="reset-actions">
        <button type="submit" disabled={loading || resetPw.length < 8}
          title={resetPw.length < 8 ? 'Enter a new password of at least 8 characters' : 'Set the new password and sign out this user’s sessions'}>Reset password</button>
        <button type="button" class="secondary" on:click={closeReset} title="Close without changing the password">Cancel</button>
      </div>
      {#if resetPw && resetPw.length < 8}<p class="small-help">Must be at least 8 characters.</p>{/if}
      {#if resetMsg}<p class="muted">{resetMsg}</p>{/if}
      {#if message}<p class="danger">{message}</p>{/if}
    </form>
  </Modal>
{/if}

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

  .reset-actions {
    display: flex;
    gap: 0.5rem;
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

  .agent-actions {
    margin-left: auto;
    display: flex;
    gap: 0.5rem;
  }

  .link-btn {
    background: none;
    border: none;
    padding: 0;
    font-size: 0.78rem;
    color: #2563eb;
    cursor: pointer;
    text-decoration: underline;
  }

  .link-btn.danger {
    color: #b3261e;
  }

  .link-btn:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .role-owner { background: #fde68a; color: #78350f; }
  .role-admin { background: #ddd6fe; color: #4c1d95; }
  .role-librarian { background: #a7f3d0; color: #065f46; }
  .role-editor { background: #bfdbfe; color: #1e3a5f; }
  .role-contributor { background: #fde9b8; color: #92400e; }
  .role-reader { background: #e2e8f0; color: #44515f; }

  .role-guide {
    margin-top: 0.9rem;
    padding-top: 0.75rem;
    border-top: 1px solid #e2e8f0;
  }

  .role-guide h3 {
    font-size: 0.85rem;
    font-weight: 600;
    margin: 0 0 0.5rem;
    color: #44515f;
  }

  .role-guide-item {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    margin-bottom: 0.4rem;
    font-size: 0.8rem;
    line-height: 1.4;
  }

  .role-guide-item .role-badge {
    flex: 0 0 auto;
  }
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

  .lock-badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.4rem;
    border-radius: 0.25rem;
    background: #fef3c7;
    color: #78350f;
    font-weight: 600;
  }

  .self-badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.4rem;
    border-radius: 0.25rem;
    background: #e0f2fe;
    color: #075985;
    font-weight: 600;
  }

  .user-list article.locked {
    border-left: 3px solid #f59e0b;
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

  .privileges {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 0.7rem;
    margin-top: 0.5rem;
  }

  .priv {
    align-items: center;
    color: #44515f;
    display: flex;
    flex-direction: row;
    font-size: 0.75rem;
    font-weight: 600;
    gap: 0.25rem;
  }

  .import-roots {
    margin-top: 1rem;
  }

  .import-roots .roots {
    border-collapse: collapse;
    width: 100%;
    margin: 0.5rem 0;
    font-size: 0.85rem;
  }

  .import-roots .roots th,
  .import-roots .roots td {
    text-align: left;
    padding: 0.35rem 0.5rem;
    border-bottom: 1px solid #e5e7eb;
    vertical-align: top;
  }

  .import-roots .roots .path {
    word-break: break-all;
  }

  .import-roots .db-badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.4rem;
    border-radius: 0.25rem;
    background: #dcfce7;
    color: #166534;
    font-weight: 600;
  }

  .import-roots .warn {
    margin-left: 0.4rem;
    font-size: 0.7rem;
    color: #b45309;
    font-weight: 600;
  }

  .import-roots .add-root {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }

  .import-roots .add-root input {
    flex: 1 1 12rem;
  }

  .import-roots button.danger {
    background: #b91c1c;
    color: white;
    border: none;
    border-radius: 0.25rem;
    padding: 0.25rem 0.6rem;
    cursor: pointer;
  }

  .import-roots button.danger:disabled {
    background: #e5e7eb;
    color: #9ca3af;
    cursor: not-allowed;
  }

  .allowed-hosts {
    margin-top: 1rem;
  }

  .allowed-hosts .hosts {
    border-collapse: collapse;
    width: 100%;
    margin: 0.5rem 0;
    font-size: 0.85rem;
  }

  .allowed-hosts .hosts th,
  .allowed-hosts .hosts td {
    text-align: left;
    padding: 0.35rem 0.5rem;
    border-bottom: 1px solid #e5e7eb;
    vertical-align: top;
  }

  .allowed-hosts .db-badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.4rem;
    border-radius: 0.25rem;
    background: #dcfce7;
    color: #166534;
    font-weight: 600;
  }

  .allowed-hosts .add-host {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }

  .allowed-hosts .add-host input {
    flex: 1 1 16rem;
  }

  .allowed-hosts button.danger {
    background: #b91c1c;
    color: white;
    border: none;
    border-radius: 0.25rem;
    padding: 0.25rem 0.6rem;
    cursor: pointer;
  }

  .allowed-hosts button.danger:disabled {
    background: #e5e7eb;
    color: #9ca3af;
    cursor: not-allowed;
  }

  .sr-only {
    border: 0;
    clip: rect(0 0 0 0);
    height: 1px;
    margin: -1px;
    overflow: hidden;
    padding: 0;
    position: absolute;
    width: 1px;
  }

  .download-policy .policy-options {
    border: none;
    display: grid;
    gap: 0.5rem;
    margin: 0.5rem 0 0;
    padding: 0;
  }

  .download-policy .policy-option {
    align-items: flex-start;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    cursor: pointer;
    display: flex;
    gap: 0.6rem;
    padding: 0.55rem 0.7rem;
  }

  .download-policy .policy-option.active {
    background: #eef2ff;
    border-color: #4f46e5;
  }

  .download-policy .policy-text {
    display: grid;
    gap: 0.15rem;
  }

  .groups {
    margin-top: 1rem;
  }

  .groups .add-group {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: 0.5rem 0 0.75rem;
  }

  .groups .add-group input {
    flex: 1 1 14rem;
  }

  .groups .group-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .groups .group-actions {
    display: flex;
    gap: 0.6rem;
    margin-left: auto;
  }

  .group-detail {
    display: grid;
    gap: 0.75rem;
    margin-top: 0.6rem;
  }

  @media (min-width: 720px) {
    .group-detail {
      grid-template-columns: 1fr 1fr;
    }
  }

  .subgroup h4,
  .defaults h4 {
    font-size: 0.82rem;
    font-weight: 600;
    margin: 0 0 0.4rem;
    color: #44515f;
  }

  .picker {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-bottom: 0.5rem;
  }

  .picker select {
    flex: 1 1 8rem;
  }

  .row-list {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .row-list li {
    align-items: center;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
    font-size: 0.85rem;
  }

  .type-badge {
    background: #e0e7ff;
    border-radius: 0.25rem;
    color: #3730a3;
    font-size: 0.7rem;
    font-weight: 600;
    padding: 0.05rem 0.35rem;
    text-transform: uppercase;
  }

  .defaults {
    border-top: 1px solid #e2e8f0;
    margin-top: 0.9rem;
    padding-top: 0.75rem;
  }

  .defaults h3 {
    font-size: 0.9rem;
    font-weight: 600;
    margin: 0 0 0.5rem;
  }

  .access-inline {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
    font-size: 0.85rem;
  }

  .access-inline select {
    flex: 1 1 16rem;
    width: auto;
  }
</style>
