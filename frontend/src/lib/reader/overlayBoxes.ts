// Geometry helpers for the reader's on-page overlay boxes (citation anchor buttons + annotation
// highlights). Boxes are stored in PDF user-space with a top-left origin (matching GROBID). The
// page canvas is rendered at pdf.js `viewport = page.getViewport({ scale })`, which scales that
// same user-space by `scale`, so a box maps to on-screen pixels by simply multiplying by `scale`.
// Deriving the box style from the *same* scale used to render keeps the boxes locked to the text
// across zoom, window resize and re-render, and lets the identical math drive both the paged and
// scroll (continuous) views — each scroll page owns its own canvas at the current scale.

import type { Annotation, CitationContext, PdfCoordinateBox } from '../../api/client';

export type CitationBox = { box: PdfCoordinateBox; context: CitationContext };
export type AnnotationBox = { box: PdfCoordinateBox; annotation: Annotation };

// Absolute-positioning CSS for a box, scaled to the rendered canvas. `scale` MUST be the scale the
// canvas was rendered at so the overlay tracks the text exactly.
export function overlayBoxStyle(box: PdfCoordinateBox, scale: number): string {
  return (
    `left:${box.x * scale}px;top:${box.y * scale}px;` +
    `width:${box.w * scale}px;height:${box.h * scale}px`
  );
}

// Citation anchor boxes that fall on the given page.
export function citationBoxesForPage(contexts: CitationContext[], page: number): CitationBox[] {
  return contexts
    .filter((c) => (c.pdf_coordinates?.length ?? 0) > 0)
    .flatMap((c) =>
      (c.pdf_coordinates ?? [])
        .filter((box) => box.page === page)
        .map((box) => ({ box, context: c })),
    );
}

// Persisted annotation highlight boxes that fall on the given page.
export function annotationBoxesForPage(annotations: Annotation[], page: number): AnnotationBox[] {
  return annotations.flatMap((a) => {
    const boxes = (a.coordinates as { boxes?: PdfCoordinateBox[] } | null)?.boxes ?? [];
    return boxes.filter((box) => box.page === page).map((box) => ({ box, annotation: a }));
  });
}
