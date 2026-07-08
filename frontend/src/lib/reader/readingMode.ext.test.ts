import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  READING_MODE_KEY,
  isReadingMode,
  readStoredReadingMode,
  readingModeFilter,
  writeReadingMode,
} from './readingMode';

describe('reading mode resilience', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('accepts only the supported reading modes', () => {
    expect(isReadingMode('original')).toBe(true);
    expect(isReadingMode('dim')).toBe(true);
    expect(isReadingMode('dark')).toBe(true);
    expect(isReadingMode('sepia')).toBe(false);
    expect(isReadingMode(null)).toBe(false);
  });

  it('returns useful no-op and transformed canvas filters', () => {
    expect(readingModeFilter('original')).toBe('none');
    expect(readingModeFilter('dim')).toContain('brightness');
    expect(readingModeFilter('dark')).toContain('invert');
  });

  it('falls back to original when stored value is missing or invalid', () => {
    expect(readStoredReadingMode()).toBe('original');
    localStorage.setItem(READING_MODE_KEY, 'unsupported');
    expect(readStoredReadingMode()).toBe('original');
  });

  it('persists valid choices without throwing when localStorage is available', () => {
    writeReadingMode('dark');
    expect(localStorage.getItem(READING_MODE_KEY)).toBe('dark');
    expect(readStoredReadingMode()).toBe('dark');
  });

  it('fails closed to original when localStorage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });

    expect(readStoredReadingMode()).toBe('original');
  });
});
