// The signed-in user, loaded from GET /auth/me. Components read this to gate UI by role
// (e.g. hide owner-only tabs, disable editing for readers) without prop-drilling.
import { derived, writable } from 'svelte/store';

import type { CurrentUser, UserRole, Work } from '../api/client';

export const currentUser = writable<CurrentUser | null>(null);

// Role ladder, lowest → highest. Capability gates compare ranks so a higher role always
// satisfies a lower floor (e.g. a librarian passes the contributor floor).
const ROLE_RANK: Record<UserRole, number> = {
  reader: 0,
  contributor: 1,
  editor: 2,
  librarian: 3,
  admin: 4,
  owner: 5,
};

/** True when the signed-in user's role is at least `floor` on the ladder. */
function roleAtLeast(user: CurrentUser | null, floor: UserRole): boolean {
  return !!user && ROLE_RANK[user.role] >= ROLE_RANK[floor];
}

/**
 * True when the signed-in user may modify library content at all — the paper-edit floor is
 * contributor (a contributor may create/edit/delete their OWN papers; editor+ any visible paper).
 * Use `canModifyWork` for the per-paper own-only decision.
 */
export const canEdit = derived(currentUser, ($u) => roleAtLeast($u, 'contributor'));

/** True when the signed-in user may create/edit/delete papers (contributor floor). */
export const canManagePapers = derived(currentUser, ($u) => roleAtLeast($u, 'contributor'));

/** True when the signed-in user is a contributor or higher. */
export const isContributor = derived(currentUser, ($u) => roleAtLeast($u, 'contributor'));

/** True when the signed-in user is an editor or higher (the tag-delete floor). */
export const isEditor = derived(currentUser, ($u) => roleAtLeast($u, 'editor'));

/** True when the signed-in user is a librarian or higher. */
export const isLibrarian = derived(currentUser, ($u) => roleAtLeast($u, 'librarian'));

/**
 * True when the signed-in user may manage library structure — create/edit/delete racks & shelves
 * and their membership. The floor is librarian (admin/owner pass too).
 */
export const canManageStructure = derived(currentUser, ($u) => roleAtLeast($u, 'librarian'));

/** True when the signed-in user may administer users/agents/AI settings/audit log (admin+). */
export const canManageUsers = derived(currentUser, ($u) => roleAtLeast($u, 'admin'));

/** True when the signed-in user is an admin (but not the owner). */
export const isAdmin = derived(currentUser, ($u) => $u?.role === 'admin');

/**
 * True when the signed-in user is THE owner — the single, immutable bootstrap account. Use this
 * for owner-exclusive gates (managing admins, locking the owner row); use `canManageUsers` for
 * general admin surfaces shared by owner and admin.
 */
export const isOwner = derived(currentUser, ($u) => $u?.role === 'owner');

/**
 * Whether the signed-in user may modify THIS paper, mirroring the backend can_modify_work rule:
 * needs the contributor floor; a contributor may only touch papers they created
 * (`work.created_by_user_id === me.id`), while editor+ may modify any visible paper. The server is
 * the source of truth — this only gates affordances so the UI doesn't offer doomed actions.
 */
export function canModifyWork(user: CurrentUser | null, work: Pick<Work, 'created_by_user_id'>): boolean {
  if (!roleAtLeast(user, 'contributor')) return false;
  if (roleAtLeast(user, 'editor')) return true;
  // Contributor: own papers only.
  return !!user && work.created_by_user_id === user.id;
}

/** Standard tooltip shown on controls disabled purely because of role. */
export const INSUFFICIENT_ROLE = 'Insufficient role for this operation';
