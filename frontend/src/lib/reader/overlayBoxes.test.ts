import { describe, expect, it } from 'vitest';

import type { Annotation, CitationContext, PdfCoordinateBox } from '../../api/client';
import { annotationBoxesForPage, citationBoxesForPage, overlayBoxStyle } from './overlayBoxes';

const box = (page: number, x: number, y: number, w = 10, h = 8): PdfCoordinateBox => ({
  page,
  x,
  y,
  w,
  h,
});

function context(id: string, boxes: PdfCoordinateBox[]): CitationContext {
  return {
    id,
    reference_id: `ref-${id}`,
    resolved_cited_work_id: null,
    reference_title: `Work ${id}`,
    reference_raw_citation: null,
    reference_doi: null,
    marker_text: '[1]',
    section_label: null,
    context_before: null,
    context_sentence: null,
    context_after: null,
    page: boxes[0]?.page ?? null,
    pdf_coordinates: boxes,
    pdf_x: null,
    pdf_y: null,
    pdf_w: null,
    pdf_h: null,
    source_tei_id: null,
  };
}

function annotation(id: string, boxes: PdfCoordinateBox[]): Annotation {
  return {
    id,
    work_id: 'w1',
    file_id: null,
    version_id: null,
    page: boxes[0]?.page ?? null,
    coordinates: { boxes },
    selected_text: 'note',
    annotation_type: 'highlight',
    content_markdown: null,
    created_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

describe('overlayBoxStyle', () => {
  it('scales a top-left PDF-space box by the render scale', () => {
    expect(overlayBoxStyle(box(1, 100, 200, 12, 10), 1.3)).toBe(
      'left:130px;top:260px;width:15.600000000000001px;height:13px',
    );
  });

  it('tracks zoom: doubling the scale doubles every dimension', () => {
    const b = box(1, 50, 60, 20, 15);
    expect(overlayBoxStyle(b, 1)).toBe('left:50px;top:60px;width:20px;height:15px');
    expect(overlayBoxStyle(b, 2)).toBe('left:100px;top:120px;width:40px;height:30px');
  });
});

describe('citationBoxesForPage', () => {
  const contexts = [
    context('a', [box(1, 10, 10), box(2, 20, 20)]),
    context('b', [box(2, 30, 30)]),
    context('empty', []),
  ];

  it('returns only boxes on the requested page, paired with their context', () => {
    const page2 = citationBoxesForPage(contexts, 2);
    expect(page2.map((r) => r.context.id)).toEqual(['a', 'b']);
    expect(page2.map((r) => r.box.x)).toEqual([20, 30]);
  });

  it('returns one entry per box on the page (multi-box contexts split)', () => {
    expect(citationBoxesForPage(contexts, 1).map((r) => r.context.id)).toEqual(['a']);
  });

  it('ignores contexts without coordinates and pages with no boxes', () => {
    expect(citationBoxesForPage(contexts, 3)).toEqual([]);
  });
});

describe('annotationBoxesForPage', () => {
  const annotations = [
    annotation('n1', [box(1, 5, 5), box(3, 7, 7)]),
    annotation('n2', [box(3, 9, 9)]),
    { ...annotation('n3', []), coordinates: null } as Annotation,
  ];

  it('returns boxes on the page paired with their annotation', () => {
    const page3 = annotationBoxesForPage(annotations, 3);
    expect(page3.map((r) => r.annotation.id)).toEqual(['n1', 'n2']);
  });

  it('tolerates null / missing coordinates', () => {
    expect(annotationBoxesForPage(annotations, 2)).toEqual([]);
  });
});
