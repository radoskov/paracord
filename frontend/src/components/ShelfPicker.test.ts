import { render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { Shelf } from '../api/client';
import ShelfPicker from './ShelfPicker.svelte';

const SHELVES: Shelf[] = [
  {
    id: 'inbox',
    name: 'Inbox',
    description: null,
    status: 'active',
    access_level: 'open',
    can_modify: true,
    is_default: true,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
  },
  {
    id: 'ml',
    name: 'Machine learning',
    description: null,
    status: 'active',
    access_level: 'open',
    can_modify: true,
    is_default: false,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
  },
];

function makeClient() {
  return { listShelves: vi.fn().mockResolvedValue(SHELVES) };
}

function optionNames(): string[] {
  const select = screen.getByLabelText('Add to shelf') as HTMLSelectElement;
  return Array.from(select.options).map((o) => o.textContent ?? '');
}

describe('ShelfPicker excludeDefault (L1)', () => {
  it('excludes the default/Inbox shelf as a move-target when excludeDefault is set', async () => {
    const client = makeClient();
    render(ShelfPicker, { client: client as never, excludeDefault: true });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await waitFor(() => expect(optionNames()).toContain('Machine learning'));
    expect(optionNames()).not.toContain('Inbox');
  });

  it('keeps the Inbox shelf visible by default (other callers unaffected)', async () => {
    const client = makeClient();
    render(ShelfPicker, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await waitFor(() => expect(optionNames()).toContain('Inbox'));
    expect(optionNames()).toContain('Machine learning');
  });
});
