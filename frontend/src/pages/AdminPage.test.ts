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
    listThemes: vi.fn().mockResolvedValue([]),
    listDefaultGrants: vi.fn().mockResolvedValue([]),
    getAccessSettings: vi.fn().mockResolvedValue({ default_access_level: 'open', allowed: ['open', 'visible', 'private'] }),
    // Reference dupes (S13/S14).
    getReferenceDupes: vi.fn().mockResolvedValue({ last_scan: null, conflicts: [] }),
    scanReferenceDupes: vi
      .fn()
      .mockResolvedValue({ queued: false, job_id: null, result: { at: null, groups_scanned: 1, folded: 2, conflicts: 0 } }),
    resolveReferenceDupe: vi.fn().mockResolvedValue({ last_scan: null, conflicts: [] }),
    getAppConfig: vi.fn().mockResolvedValue({
      max_papers_per_page: 500,
      rate_limit_per_client_per_min: 60,
      rate_limit_global_per_min: 300,
      max_batch_items: 100,
      rq_worker_count: 2,
      max_queue_len: 1000,
      citing_papers_fetch_cap: 1000,
      ai_scope_job_threshold: 100,
      citation_graph_node_cap: 1500,
      topic_graph_node_cap: 400,
      viz_node_cap: 500,
      use_fuzzy_match_as_confirmed: false,
      reference_rescan_on_startup: false,
    }),
    updateAppConfig: vi.fn().mockImplementation(async (changes) => ({
      max_papers_per_page: 250,
      rate_limit_per_client_per_min: 60,
      rate_limit_global_per_min: 300,
      max_batch_items: 100,
      rq_worker_count: 2,
      max_queue_len: 1000,
      citing_papers_fetch_cap: 1000,
      ai_scope_job_threshold: 100,
      citation_graph_node_cap: 1500,
      topic_graph_node_cap: 400,
      viz_node_cap: 500,
      use_fuzzy_match_as_confirmed: false,
      reference_rescan_on_startup: false,
      ...changes,
    })),
    rescanAllReferences: vi.fn().mockResolvedValue({}),
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

  it('loads a bundled theme YAML into the editor as a template', async () => {
    const client = makeClient();
    render(AdminPage, { client: client as never });
    await waitFor(() => expect(client.listThemes).toHaveBeenCalled());

    await fireEvent.click(screen.getByRole('button', { name: 'Themes' }));

    const picker = screen.getByLabelText('Load existing as template') as HTMLSelectElement;
    await fireEvent.change(picker, { target: { value: 'mocha-cool' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Load into editor' }));

    const editor = screen.getByLabelText('Theme YAML') as HTMLTextAreaElement;
    await waitFor(() => expect(editor.value).toContain('id: mocha-cool'));
    // Bundled sources are compiled in — no backend round-trip.
    expect(editor.value).toContain('selected-border');
  });

  it('fetches a custom theme YAML source when loaded as a template', async () => {
    const client = makeClient({
      listThemes: vi.fn().mockResolvedValue([
        {
          id: 'my-theme',
          name: 'My Theme',
          mode: 'dark',
          temperature: 'cool',
          swatch: { surface: '#111', primary: '#89b4fa', accents: ['#89b4fa'] },
        },
      ]),
      getThemeSource: vi.fn().mockResolvedValue({ id: 'my-theme', yaml: 'id: my-theme\nname: "My Theme"\n' }),
    });
    render(AdminPage, { client: client as never });
    await waitFor(() => expect(client.listThemes).toHaveBeenCalled());

    await fireEvent.click(screen.getByRole('button', { name: 'Themes' }));

    const picker = screen.getByLabelText('Load existing as template') as HTMLSelectElement;
    await fireEvent.change(picker, { target: { value: 'my-theme' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Load into editor' }));

    await waitFor(() => expect(client.getThemeSource).toHaveBeenCalledWith('my-theme'));
    const editor = screen.getByLabelText('Theme YAML') as HTMLTextAreaElement;
    await waitFor(() => expect(editor.value).toContain('id: my-theme'));
  });

  it('saves the global max papers per page from the Settings tab', async () => {
    const client = makeClient();
    render(AdminPage, { client: client as never });
    await waitFor(() => expect(client.getAppConfig).toHaveBeenCalled());

    await fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const input = screen.getByLabelText('Global max papers per page') as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe('500'));

    await fireEvent.input(input, { target: { value: '250' } });
    // The Settings tab has a Save button per section (Library + Overload protection).
    await fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[0]);

    await waitFor(() =>
      expect(client.updateAppConfig).toHaveBeenCalledWith({ max_papers_per_page: 250 }),
    );
  });

  it('retries a failed app-config load so the Settings form still seeds', async () => {
    const client = makeClient();
    const config = await client.getAppConfig();
    client.getAppConfig = vi
      .fn()
      .mockRejectedValueOnce(new Error('transient'))
      .mockResolvedValue(config);
    render(AdminPage, { client: client as never });

    await fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const input = screen.getByLabelText('Global max papers per page') as HTMLInputElement;
    // First attempt rejects; the retry (after its backoff) seeds the field.
    await waitFor(() => expect(input.value).toBe('500'), { timeout: 4000 });
    expect(client.getAppConfig).toHaveBeenCalledTimes(2);
  });

  it('saves the overload-protection settings from the Settings tab', async () => {
    const client = makeClient();
    render(AdminPage, { client: client as never });
    await waitFor(() => expect(client.getAppConfig).toHaveBeenCalled());

    await fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const workers = screen.getByLabelText('Background worker processes') as HTMLInputElement;
    await waitFor(() => expect(workers.value).toBe('2'));

    await fireEvent.input(workers, { target: { value: '4' } });
    await fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[1]);

    await waitFor(() =>
      expect(client.updateAppConfig).toHaveBeenCalledWith({
        rate_limit_per_client_per_min: 60,
        rate_limit_global_per_min: 300,
        max_batch_items: 100,
        rq_worker_count: 4,
        max_queue_len: 1000,
        citing_papers_fetch_cap: 1000,
        ai_scope_job_threshold: 100,
        citation_graph_node_cap: 1500,
        topic_graph_node_cap: 400,
        viz_node_cap: 500,
      }),
    );
  });

  it('saves the reference-matching toggle from the Settings tab', async () => {
    const client = makeClient();
    render(AdminPage, { client: client as never });
    await waitFor(() => expect(client.getAppConfig).toHaveBeenCalled());

    await fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const toggle = screen.getByLabelText(
      'Treat a fuzzy “likely local” match as confirmed',
    ) as HTMLInputElement;
    await waitFor(() => expect(toggle.checked).toBe(false));

    await fireEvent.click(toggle);
    await fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[2]);

    await waitFor(() =>
      expect(client.updateAppConfig).toHaveBeenCalledWith({
        use_fuzzy_match_as_confirmed: true,
        reference_rescan_on_startup: false,
      }),
    );
  });

  it('triggers a whole-library reference rescan from the Settings tab', async () => {
    const client = makeClient();
    render(AdminPage, { client: client as never });
    await waitFor(() => expect(client.getAppConfig).toHaveBeenCalled());

    await fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Rescan whole library now' }),
    );

    await waitFor(() => expect(client.rescanAllReferences).toHaveBeenCalled());
  });
  it('scans reference dupes from the Reference dupes tab', async () => {
    const client = makeClient();
    render(AdminPage, { client: client as never });
    await waitFor(() => expect(client.listAdminUsers).toHaveBeenCalled());

    await fireEvent.click(screen.getByRole('button', { name: 'Reference dupes' }));
    await waitFor(() => expect(client.getReferenceDupes).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText('No contradictions to review.')).toBeTruthy());

    await fireEvent.click(screen.getByRole('button', { name: 'Scan for duplicate references' }));
    await waitFor(() => expect(client.scanReferenceDupes).toHaveBeenCalled());
    await waitFor(() =>
      expect(
        screen.getByText('2 reference dupe(s) resolved, 0 contradiction(s) found.'),
      ).toBeTruthy(),
    );
  });

});
