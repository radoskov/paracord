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
    expect(readingModeFilter('dim')).toBe('sepia(0.35) brightness(0.93) contrast(0.95)');
    expect(readingModeFilter('dark')).toBe(
      'invert(1) hue-rotate(180deg) brightness(0.92) contrast(0.95)',
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
