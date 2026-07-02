import { describe, expect, it } from 'vitest';

import type { CurrentUser, UserRole, Work } from '../api/client';
import { canModifyWork } from './session';

function user(role: UserRole, id = 'me'): CurrentUser {
  return {
    id,
    username: role,
    role,
    display_name: null,
    email: null,
    created_at: null,
    last_login_at: null,
    papers_per_page: null,
  };
}

function work(createdBy: string | null): Pick<Work, 'created_by_user_id'> {
  return { created_by_user_id: createdBy };
}

describe('canModifyWork', () => {
  it('a reader can never modify a paper', () => {
    expect(canModifyWork(user('reader'), work('me'))).toBe(false);
    expect(canModifyWork(user('reader'), work(null))).toBe(false);
  });

  it('a contributor may modify only their OWN papers', () => {
    const me = user('contributor', 'me');
    expect(canModifyWork(me, work('me'))).toBe(true);
    expect(canModifyWork(me, work('someone-else'))).toBe(false);
    // A loose/system paper (no creator) is not owned by the contributor.
    expect(canModifyWork(me, work(null))).toBe(false);
  });

  it('an editor may modify any visible paper, regardless of creator', () => {
    const me = user('editor', 'me');
    expect(canModifyWork(me, work('me'))).toBe(true);
    expect(canModifyWork(me, work('someone-else'))).toBe(true);
    expect(canModifyWork(me, work(null))).toBe(true);
  });

  it('librarian / admin / owner also modify any paper (ladder)', () => {
    for (const role of ['librarian', 'admin', 'owner'] as UserRole[]) {
      expect(canModifyWork(user(role), work('someone-else'))).toBe(true);
    }
  });

  it('returns false when there is no signed-in user', () => {
    expect(canModifyWork(null, work('me'))).toBe(false);
  });
});
