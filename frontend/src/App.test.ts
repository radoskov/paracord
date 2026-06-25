import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import App from './App.svelte';

describe('App', () => {
  it('renders the PaperRacks shell and the sign-in form when unauthenticated', () => {
    // No token in localStorage -> the app shows the login view without any network calls.
    render(App);
    expect(screen.getByText('PaperRacks')).toBeTruthy();
    expect(screen.getByRole('button', { name: /sign in/i })).toBeTruthy();
  });
});
