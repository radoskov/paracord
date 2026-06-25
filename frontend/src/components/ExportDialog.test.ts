import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import { EXPORT_FORMATS } from '../api/client';
import ExportDialog from './ExportDialog.svelte';

describe('ExportDialog', () => {
  it('lists every export format and exports the selected one', async () => {
    const onExport = vi.fn();
    render(ExportDialog, { label: 'shelf "Refs"', onExport });

    // The scope label and one option per supported format are rendered.
    expect(screen.getByText(/shelf "Refs"/)).toBeTruthy();
    const options = screen.getAllByRole('option') as HTMLOptionElement[];
    expect(options.map((o) => o.value)).toEqual(EXPORT_FORMATS.map((f) => f.value));

    // Default format exports as bibtex; after selecting RIS it exports ris.
    await fireEvent.click(screen.getByRole('button', { name: /export/i }));
    expect(onExport).toHaveBeenLastCalledWith('bibtex');

    await fireEvent.change(screen.getByRole('combobox'), { target: { value: 'ris' } });
    await fireEvent.click(screen.getByRole('button', { name: /export/i }));
    expect(onExport).toHaveBeenLastCalledWith('ris');
  });
});
