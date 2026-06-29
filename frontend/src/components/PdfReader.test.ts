import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import type { CitationContext } from '../api/client';
import PdfReader from './PdfReader.svelte';

const CONTEXT: CitationContext = {
  id: 'c1',
  reference_id: 'r1',
  resolved_cited_work_id: null,
  reference_title: 'A Cited Work',
  reference_raw_citation: null,
  reference_doi: null,
  marker_text: '[1]',
  section_label: 'Introduction',
  context_before: null,
  context_sentence: 'As shown previously [1].',
  context_after: null,
  page: 3,
  pdf_coordinates: [{ page: 3, x: 120, y: 450, w: 12, h: 10 }],
  pdf_x: 120,
  pdf_y: 450,
  pdf_w: 12,
  pdf_h: 10,
  source_tei_id: null,
};

describe('PdfReader', () => {
  // No fileUrl → the PDF.js path is never imported, keeping this deterministic in jsdom.
  it('prompts to open a PDF when no URL is set', () => {
    render(PdfReader, { fileId: 'abcdef123456', fileName: 'paper.pdf', fileUrl: null });
    expect(screen.getByText(/open a pdf/i)).toBeTruthy();
  });

  it('lists citation contexts with a jump control on the References tab', async () => {
    render(PdfReader, {
      fileId: 'abcdef123456',
      fileName: 'paper.pdf',
      fileUrl: null,
      contexts: [CONTEXT],
    });
    await fireEvent.click(screen.getByRole('button', { name: /references/i }));
    expect(screen.getByText('As shown previously [1].')).toBeTruthy();
    expect(screen.getByText('A Cited Work')).toBeTruthy();
    expect(screen.getByRole('button', { name: /jump to p\.3/i })).toBeTruthy();
  });
});
