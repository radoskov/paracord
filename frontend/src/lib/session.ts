// The signed-in user, loaded from GET /auth/me. Components read this to gate UI by role
// (e.g. hide owner-only tabs, disable editing for readers) without prop-drilling.
import { derived, writable } from 'svelte/store';

import type { CurrentUser, UserRole } from '../api/client';

export const currentUser = writable<CurrentUser | null>(null);

/** Roles permitted to mutate library content (create/edit/delete/enrich). */
const EDIT_ROLES: UserRole[] = ['owner', 'admin', 'editor'];

/** Roles permitted to administer users/agents/AI settings/audit log. */
const ADMIN_ROLES: UserRole[] = ['owner', 'admin'];

/** True when the signed-in user may modify library content. */
export const canEdit = derived(currentUser, ($u) => !!$u && EDIT_ROLES.includes($u.role));

/** True when the signed-in user may reach the admin surfaces (owner or admin). */
export const canManageUsers = derived(
  currentUser,
  ($u) => !!$u && ADMIN_ROLES.includes($u.role),
);

/** True when the signed-in user is an admin (but not the owner). */
export const isAdmin = derived(currentUser, ($u) => $u?.role === 'admin');

/**
 * True when the signed-in user is THE owner — the single, immutable bootstrap account. Use this
 * for owner-exclusive gates (managing admins, locking the owner row); use `canManageUsers` for
 * general admin surfaces shared by owner and admin.
 */
export const isOwner = derived(currentUser, ($u) => $u?.role === 'owner');

/** Standard tooltip shown on controls disabled purely because of role. */
export const INSUFFICIENT_ROLE = 'Insufficient role for this operation';
