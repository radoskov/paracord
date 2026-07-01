import { render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App.svelte';

describe('App', () => {
  beforeEach(() => {
    // Pages load data on mount; reject network fast so authenticated renders are deterministic.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('no network'))),
    );
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('renders the PaRacORD shell and the sign-in form when unauthenticated', () => {
    render(App);
    expect(screen.getByText('PaRacORD')).toBeTruthy();
    expect(screen.getByRole('button', { name: /sign in/i })).toBeTruthy();
  });

  it('shows the tab navigation when authenticated', () => {
    window.localStorage.setItem('paracord_token', 'test-token');
    render(App);
    // The always-available section tabs (no role gate) are present...
    expect(screen.getByRole('link', { name: 'Library' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Shelves' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Profile' })).toBeTruthy();
    // ...role-gated tabs stay hidden until /auth/me confirms a sufficient role
    // (fetch is stubbed to reject here, so the role is unknown). Editor+ tabs:
    expect(screen.queryByRole('link', { name: 'Import' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Jobs' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Duplicates' })).toBeNull();
    // ...and the owner/admin-only Admin + AI & Models tabs:
    expect(screen.queryByRole('link', { name: 'Admin' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'AI & Models' })).toBeNull();
    // ...and the active tab's explanatory hint is shown.
    expect(screen.getByText(/Search, read, edit and organise/i)).toBeTruthy();
  });
});
