import { render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import WorkPicker from './WorkPicker.svelte';

function makeClient() {
  return {
    listWorks: vi.fn().mockResolvedValue({
      items: [{ id: 'w2', canonical_title: 'Attention Is All You Need', year: 2017, doi: null }],
      total: 1,
      page: 1,
      pages: 1,
    }),
  };
}

describe('WorkPicker autofocus + initial query (batch10 #6)', () => {
  it('seeds the query, runs an initial search, and focuses the input', async () => {
    const client = makeClient();
    render(WorkPicker, {
      client: client as never,
      excludeId: 'w1',
      autofocusInput: true,
      initialQuery: 'Attention',
    } as never);

    const input = screen.getByRole('textbox') as HTMLInputElement;
    expect(input.value).toBe('Attention');
    // Seeded query triggers a search immediately (no debounce wait needed).
    await waitFor(() => expect(client.listWorks).toHaveBeenCalledWith({ q: 'Attention' }));
    await waitFor(() => expect(screen.getByText('Attention Is All You Need')).toBeTruthy());
    // Autofocus lands the cursor in the box.
    await waitFor(() => expect(document.activeElement).toBe(input));
  });

  it('does not search or focus without an initial query', async () => {
    const client = makeClient();
    render(WorkPicker, { client: client as never, excludeId: 'w1' } as never);
    const input = screen.getByRole('textbox') as HTMLInputElement;
    expect(input.value).toBe('');
    expect(client.listWorks).not.toHaveBeenCalled();
  });
});
