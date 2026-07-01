import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import { CITATION_STYLES, EXPORT_FORMATS, type ExportResponse } from '../api/client';
import ExportDialog from './ExportDialog.svelte';

const okExport = (): Promise<ExportResponse> =>
  Promise.resolve({ filename: 'x.txt', content_type: 'text/plain', content: '' });

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

  it('loads citation styles dynamically from the backend for the styled format', async () => {
    const fetchStyles = vi.fn().mockResolvedValue([
      { value: 'apa', label: 'APA (7th edition)' },
      { value: 'ieee', label: 'IEEE' },
      { value: 'vancouver', label: 'Vancouver' },
    ]);
    render(ExportDialog, { label: 'shelf', fetchExport: okExport, fetchStyles });

    // Switch the format selector to "styled" to reveal the style dropdown.
    const [formatSelect] = screen.getAllByRole('combobox') as HTMLSelectElement[];
    await fireEvent.change(formatSelect, { target: { value: 'styled' } });

    // The style options come from the backend list (labels, not hard-coded upper-cased keys).
    await waitFor(() => expect(screen.getByRole('option', { name: 'Vancouver' })).toBeTruthy());
    expect(fetchStyles).toHaveBeenCalled();
    expect(screen.getByRole('option', { name: 'APA (7th edition)' })).toBeTruthy();
  });

  it('falls back to the static style list when no fetchStyles is provided', async () => {
    render(ExportDialog, { label: 'shelf', fetchExport: okExport });
    const [formatSelect] = screen.getAllByRole('combobox') as HTMLSelectElement[];
    await fireEvent.change(formatSelect, { target: { value: 'styled' } });
    // Every static fallback style is offered (e.g. the newly added MLA / Vancouver).
    for (const s of CITATION_STYLES) {
      expect(screen.getByRole('option', { name: s.label })).toBeTruthy();
    }
  });
});
