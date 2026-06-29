import { describe, expect, it } from 'vitest';

describe.skip('future browser-level literature review flow', () => {
  it('imports, organizes, reads, annotates, graphs, summarizes, and exports a rack', async () => {
    // Replace with Playwright or a real component-level flow when the UI becomes
    // feature-complete. This disabled test preserves the acceptance target.
    const intendedFlow = [
      'login',
      'add server folder',
      'import papers',
      'create shelf',
      'create rack',
      'read PDF',
      'add separate annotation',
      'show citation graph scoped to rack',
      'create rack summary',
      'export rack citations',
    ];

    expect(intendedFlow).toContain('export rack citations');
  });
});
