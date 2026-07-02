import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { currentUser } from '../lib/session';
import AdminPage from './AdminPage.svelte';

// A client stub whose admin reads resolve to small fixtures so the page can render fully.
function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    listAdminUsers: vi.fn().mockResolvedValue([
      { id: 'u1', username: 'alice', role: 'reader', created_at: '2024-01-01T00:00:00Z', disabled_at: null, is_bootstrap: false },
    ]),
    listAgents: vi.fn().mockResolvedValue([]),
    listWebFindAllowedHosts: vi.fn().mockResolvedValue([]),
    // Groups + grant pickers + defaults (Phase H).
    listGroups: vi.fn().mockResolvedValue([
      { id: 'g1', name: 'nlp-team', is_personal: false, personal_user_id: null, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' },
      { id: 'g2', name: 'alice', is_personal: true, personal_user_id: 'u1', created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' },
    ]),
    listRacks: vi.fn().mockResolvedValue([]),
    listShelves: vi.fn().mockResolvedValue([]),
    listDefaultGrants: vi.fn().mockResolvedValue([]),
    getAccessSettings: vi.fn().mockResolvedValue({ default_access_level: 'open', allowed: ['open', 'visible', 'private'] }),
    getAppConfig: vi.fn().mockResolvedValue({ max_papers_per_page: 500 }),
    updateAppConfig: vi.fn().mockResolvedValue({ max_papers_per_page: 250 }),
    ...overrides,
  };
}

describe('AdminPage groups section', () => {
  beforeEach(() => {
    currentUser.set({ id: 'admin', username: 'admin', role: 'admin' } as never);
  });
  afterEach(() => currentUser.set(null));

  it('renders the Groups & access section and lists groups for an admin', async () => {
    const client = makeClient();
    render(AdminPage, { client: client as never });

    // The admin-gated group reads are issued on mount.
    await waitFor(() => expect(client.listGroups).toHaveBeenCalled());

    // The admin page is organised into sub-tabs (#24); open the Groups tab.
    await fireEvent.click(screen.getByRole('button', { name: 'Groups' }));

    expect(screen.getByText(/Groups & access/i)).toBeTruthy();
    // Both a shared group and a personal group render (personal is marked + non-deletable).
    expect(await screen.findByText('nlp-team')).toBeTruthy();
    expect(screen.getByText('personal')).toBeTruthy();
    // The Defaults subsection with its default-access-level control renders too.
    expect(screen.getByLabelText('Default access level')).toBeTruthy();
  });

  it('saves the global max papers per page from the Settings tab', async () => {
    const client = makeClient();
    render(AdminPage, { client: client as never });
    await waitFor(() => expect(client.getAppConfig).toHaveBeenCalled());

    await fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const input = screen.getByLabelText('Global max papers per page') as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe('500'));

    await fireEvent.input(input, { target: { value: '250' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(client.updateAppConfig).toHaveBeenCalledWith({ max_papers_per_page: 250 }),
    );
  });
});
