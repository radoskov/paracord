import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Work, WorkShelfMembership } from '../api/client';
import { currentUser } from '../lib/session';
import WorkDetail from './WorkDetail.svelte';

function makeWork(overrides: Partial<Work> = {}): Work {
  return {
    id: 'w1',
    canonical_title: 'Attention Is All You Need',
    abstract: 'transformers',
    doi: null,
    arxiv_id: null,
    venue: null,
    year: 2017,
    reading_status: 'unread',
    canonical_metadata_source: null,
    confirmed_fields: [],
    keywords: [],
    topics: [],
    created_by_user_id: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  } as unknown as Work;
}

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    listWorkMetadata: vi.fn().mockResolvedValue([]),
    listWorkFiles: vi.fn().mockResolvedValue([]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue([]),
    listTags: vi.fn().mockResolvedValue([]),
    listShelves: vi.fn().mockResolvedValue([]),
    listWorkShelves: vi.fn().mockResolvedValue([]),
    addWorkToShelf: vi.fn().mockResolvedValue(undefined),
    removeWorkFromShelf: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function membership(overrides: Partial<WorkShelfMembership> = {}): WorkShelfMembership {
  return {
    id: 's1',
    name: 'Transformers',
    access_level: 'open',
    can_modify: true,
    racks: [{ id: 'r1', name: 'Deep Learning' }],
    ...overrides,
  };
}

// Open the "Organization — where is this?" <details> so its lazy loadLocations() runs.
async function openOrganization(): Promise<void> {
  const summary = screen.getByText('Organization — where is this?');
  const details = summary.closest('details') as HTMLDetailsElement;
  details.open = true;
  await fireEvent(details, new Event('toggle'));
}

// The initial loadDetail() sets `loading` true while the mocked reads resolve; role-gated buttons
// are disabled until it settles. Wait for the Put-into button to leave the loading-disabled state.
async function settled(): Promise<void> {
  // listWorkMetadata is the first awaited read in loadDetail; once it resolved a tick has passed.
  await waitFor(() => {
    const btn = screen.queryByRole('button', { name: 'Put into…' });
    return expect(btn).toBeTruthy();
  });
}

// The Remove button inside the locations list (the Tags block also has a "Remove" button, so scope
// the query to the .location-row entry rather than the whole document).
function locationRemoveButton(): HTMLButtonElement {
  const row = document.querySelector('.location-row') as HTMLElement;
  return row.querySelector('button') as HTMLButtonElement;
}

describe('WorkDetail shelf membership (Phase N)', () => {
  beforeEach(() => {
    currentUser.set({ id: 'u1', username: 'lib', role: 'librarian' } as never);
  });

  it('renders rack › shelf rows from listWorkShelves on open', async () => {
    const client = makeClient({ listWorkShelves: vi.fn().mockResolvedValue([membership()]) });
    render(WorkDetail, { client: client as never, work: makeWork() });

    await openOrganization();
    await waitFor(() => expect(client.listWorkShelves).toHaveBeenCalledWith('w1'));
    await waitFor(() => expect(screen.getByText('Deep Learning')).toBeTruthy());
    expect(screen.getByText('Transformers')).toBeTruthy();
  });

  it('Remove calls removeWorkFromShelf and refetches the locations', async () => {
    const listWorkShelves = vi.fn().mockResolvedValue([membership()]);
    const client = makeClient({ listWorkShelves });
    render(WorkDetail, { client: client as never, work: makeWork() });

    await openOrganization();
    await waitFor(() => expect(screen.getByText('Transformers')).toBeTruthy());

    await fireEvent.click(locationRemoveButton());
    expect(client.removeWorkFromShelf).toHaveBeenCalledWith('s1', 'w1');
    // One initial load + one reload after removal.
    await waitFor(() => expect(listWorkShelves).toHaveBeenCalledTimes(2));
  });

  it('disables Remove when the shelf is not modifiable (can_modify=false)', async () => {
    const client = makeClient({
      listWorkShelves: vi.fn().mockResolvedValue([membership({ can_modify: false })]),
    });
    render(WorkDetail, { client: client as never, work: makeWork() });

    await openOrganization();
    await waitFor(() => expect(screen.getByText('Transformers')).toBeTruthy());
    expect(locationRemoveButton().disabled).toBe(true);
  });

  it('gates the Put into… button below the librarian floor', async () => {
    currentUser.set({ id: 'u1', username: 'ed', role: 'editor' } as never);
    render(WorkDetail, { client: makeClient() as never, work: makeWork() });
    await settled();
    // Editor is below the librarian floor: disabled regardless of the loading state.
    const btn = screen.getByRole('button', { name: 'Put into…' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('enables the Put into… button for a librarian', async () => {
    render(WorkDetail, { client: makeClient() as never, work: makeWork() });
    // Wait for the initial loadDetail() to clear the `loading` gate.
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Put into…' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
  });

  it('the Put-into popup adds the paper and refreshes the locations', async () => {
    const listWorkShelves = vi.fn().mockResolvedValue([]);
    const client = makeClient({
      listWorkShelves,
      listShelves: vi
        .fn()
        .mockResolvedValue([
          { id: 's9', name: 'Target', access_level: 'open', can_modify: true } as never,
        ]),
    });
    render(WorkDetail, { client: client as never, work: makeWork() });

    await openOrganization();
    // Wait until the initial load clears the loading gate so Put into… is clickable.
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Put into…' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Put into…' }));

    // Choose the shelf in the picker, then Add.
    const select = (await screen.findByLabelText('Add to shelf')) as HTMLSelectElement;
    await fireEvent.change(select, { target: { value: 's9' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Add' }));

    expect(client.addWorkToShelf).toHaveBeenCalledWith('s9', 'w1');
    // Initial open load + reload after add.
    await waitFor(() => expect(listWorkShelves).toHaveBeenCalledTimes(2));
  });
});
