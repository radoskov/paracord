// Opt-in "reading mode" for the PDF reader page canvas. The chosen filter is applied to the
// rendered page canvas ONLY (never the text-selection or highlight/annotation overlays), so
// highlights and selection stay true while the page itself is eased on the eyes.

export type ReadingMode = 'original' | 'dim' | 'dark';

export const READING_MODE_KEY = 'paracord.reader.readingMode';

const READING_MODES: readonly ReadingMode[] = ['original', 'dim', 'dark'];

// CSS `filter` string per mode. `original` is a no-op so the document renders true-to-original.
// `dim` warms the page toward a soft cream and takes the edge off the brightness. `dark` is a
// smart invert (white↔black flip) with a 180° hue rotation so hues stay roughly correct.
const READING_MODE_FILTERS: Record<ReadingMode, string> = {
  original: 'none',
  dim: 'sepia(0.35) brightness(0.93) contrast(0.95)',
  dark: 'invert(1) hue-rotate(180deg) brightness(0.92) contrast(0.95)',
};

export function readingModeFilter(mode: ReadingMode): string {
  return READING_MODE_FILTERS[mode];
}

export function isReadingMode(value: unknown): value is ReadingMode {
  return typeof value === 'string' && (READING_MODES as readonly string[]).includes(value);
}

export function readStoredReadingMode(): ReadingMode {
  try {
    const stored = localStorage.getItem(READING_MODE_KEY);
    return isReadingMode(stored) ? stored : 'original';
  } catch {
    return 'original';
  }
}

export function writeReadingMode(mode: ReadingMode): void {
  try {
    localStorage.setItem(READING_MODE_KEY, mode);
  } catch {
    // localStorage may be unavailable (private mode) — keep the in-memory choice.
  }
}
