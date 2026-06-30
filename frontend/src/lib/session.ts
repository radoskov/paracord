// The signed-in user, loaded from GET /auth/me. Components read this to gate UI by role
// (e.g. hide owner-only tabs, disable editing for readers) without prop-drilling.
import { derived, writable } from 'svelte/store';

import type { CurrentUser, UserRole } from '../api/client';

export const currentUser = writable<CurrentUser | null>(null);

/** Roles permitted to mutate library content (create/edit/delete/enrich). */
const EDIT_ROLES: UserRole[] = ['owner', 'editor'];

/** True when the signed-in user may modify library content. */
export const canEdit = derived(currentUser, ($u) => !!$u && EDIT_ROLES.includes($u.role));

/** True when the signed-in user is an owner (admin operations). */
export const isOwner = derived(currentUser, ($u) => $u?.role === 'owner');

/** Standard tooltip shown on controls disabled purely because of role. */
export const INSUFFICIENT_ROLE = 'Insufficient role for this operation';
