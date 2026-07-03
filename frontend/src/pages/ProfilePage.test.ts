import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CurrentUser } from '../api/client';
import { currentUser } from '../lib/session';
import { activeThemeId } from '../lib/theme/store';
import { get } from 'svelte/store';
import ProfilePage from './ProfilePage.svelte';

const ME: CurrentUser = {
  id: 'u1',
  username: 'owner',
  role: 'owner',
  display_name: null,
  email: null,
  created_at: null,
  last_login_at: null,
  papers_per_page: null,
  theme: null,
};

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    updateProfile: vi.fn().mockImplementation(async (changes) => ({ ...ME, ...changes })),
    changePassword: vi.fn(),
    ...overrides,
  };
}

describe('ProfilePage theme picker', () => {
  beforeEach(() => {
    localStorage.clear();
    currentUser.set(ME);
  });
  afterEach(() => {
    localStorage.clear();
    currentUser.set(null);
  });

  it('renders a data-driven option for every bundled theme, grouped Light / Dark', () => {
    render(ProfilePage, { client: makeClient() as never });
    expect(screen.getByText('Light')).toBeTruthy();
    expect(screen.getByText('Dark')).toBeTruthy();
    // Each of the four themes has a labelled option button.
    for (const name of ['Latte (warm)', 'Latte (cool)', 'Mocha (warm)', 'Mocha (cool)']) {
      expect(screen.getByTitle(`Use the ${name} theme`)).toBeTruthy();
    }
  });

  it('selecting a theme restyles the app live and persists it to the profile', async () => {
    const client = makeClient();
    render(ProfilePage, { client: client as never });

    await fireEvent.click(screen.getByTitle('Use the Mocha (cool) theme'));

    // Live restyle: the app's data-theme flips and the store publishes the new id immediately.
    expect(document.documentElement.getAttribute('data-theme')).toBe('mocha-cool');
    expect(get(activeThemeId)).toBe('mocha-cool');
    // Persisted to the server profile + cached locally for no-flash boot.
    expect(client.updateProfile).toHaveBeenCalledWith({ theme: 'mocha-cool' });
    expect(localStorage.getItem('paracord-theme')).toBe('mocha-cool');
    await waitFor(() => expect(screen.getByText('Theme saved.')).toBeTruthy());
  });
});
