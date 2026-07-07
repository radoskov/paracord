import { afterEach, describe, expect, it } from 'vitest';

import {
  READING_MODE_KEY,
  isReadingMode,
  readStoredReadingMode,
  readingModeFilter,
  writeReadingMode,
} from './readingMode';

describe('readingMode', () => {
  afterEach(() => localStorage.removeItem(READING_MODE_KEY));

  it('maps each mode to the expected CSS filter string', () => {
    expect(readingModeFilter('original')).toBe('none');
    // Dim is a warm, barely-dimmed cream (higher sepia + faint saturate, brightness near 1).
    expect(readingModeFilter('dim')).toBe('sepia(0.5) saturate(1.12) brightness(0.98) contrast(0.96)');
    // Dark is a PARTIAL invert (white→warm dark grey, not black) so the page reads like the warm
    // dark theme surface, with the hue-rotate keeping colours and a sepia warming the field.
    expect(readingModeFilter('dark')).toBe(
      'invert(0.82) hue-rotate(180deg) sepia(0.28) brightness(1.02)',
    );
  });

  it('defaults to original when nothing is stored', () => {
    expect(readStoredReadingMode()).toBe('original');
  });

  it('persists a chosen mode and reads it back', () => {
    writeReadingMode('dark');
    expect(localStorage.getItem(READING_MODE_KEY)).toBe('dark');
    expect(readStoredReadingMode()).toBe('dark');

    writeReadingMode('dim');
    expect(readStoredReadingMode()).toBe('dim');
  });

  it('falls back to original for an unrecognised stored value', () => {
    localStorage.setItem(READING_MODE_KEY, 'neon');
    expect(readStoredReadingMode()).toBe('original');
  });

  it('validates known modes', () => {
    expect(isReadingMode('original')).toBe(true);
    expect(isReadingMode('dim')).toBe(true);
    expect(isReadingMode('dark')).toBe(true);
    expect(isReadingMode('sepia')).toBe(false);
    expect(isReadingMode(null)).toBe(false);
  });
});
