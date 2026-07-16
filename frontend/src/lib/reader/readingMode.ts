// Opt-in "reading mode" for the PDF reader page canvas. The chosen filter is applied to the
// rendered page canvas ONLY (never the text-selection or highlight/annotation overlays), so
// highlights and selection stay true while the page itself is eased on the eyes.

export type ReadingMode = 'original' | 'dim' | 'dark';

export const READING_MODE_KEY = 'paracord.reader.readingMode';

const READING_MODES: readonly ReadingMode[] = ['original', 'dim', 'dark'];

// Tunable filter constants (see below for how they compose into each mode's CSS `filter`).
//
// DIM — a soft, warm cream page, barely dimmed. Higher `sepia` + a touch of `saturate` push the
// warmth (yellow) up; `brightness` sits close to 1 so the page reads light. A blank white page
// lands around a warm cream ≈ #faf9f2.
const DIM_FILTER = 'sepia(0.5) saturate(1.12) brightness(0.98) contrast(0.96)';
// DARK — a yellowish dark-grey "paper", not near-black, to match the warm dark theme surface
// (mocha-warm base ≈ #211e2a). A full `invert(1)` maps white→black; instead we invert *partially*
// (`invert(0.82)`) so white lands on a dark grey (CSS invert(a) maps white→1-a ≈ 0.18) and black
// lifts to a warm-light text (≈0.82). `hue-rotate(180deg)` keeps colours roughly correct through
// the invert, `sepia` warms the grey, and a small `brightness` lift nudges the field toward the
// target ≈ #332f2d (a warm dark grey in the #2a2632–#332f3a range). Text stays clearly AA-readable.
const DARK_FILTER = 'invert(0.82) hue-rotate(180deg) sepia(0.28) brightness(1.02)';

// CSS `filter` string per mode. `original` is a no-op so the document renders true-to-original.
const READING_MODE_FILTERS: Record<ReadingMode, string> = {
  original: 'none',
  dim: DIM_FILTER,
  dark: DARK_FILTER,
};

/** The CSS `filter` value to apply to the reader's page canvas for the given mode. */
export function readingModeFilter(mode: ReadingMode): string {
  return READING_MODE_FILTERS[mode];
}

/** Type guard for a stored/user-supplied reading-mode value. */
export function isReadingMode(value: unknown): value is ReadingMode {
  return typeof value === 'string' && (READING_MODES as readonly string[]).includes(value);
}

/** Read the persisted reading-mode choice, defaulting to 'original' if unset/invalid/unavailable. */
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
