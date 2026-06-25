import { afterEach, expect, it, vi } from 'vitest';

// Regression guard for the blank-page bug: the entrypoint must actually mount the app into
// #app. Under Svelte 5, `new App({...})` (the Svelte 4 API) throws and leaves #app empty;
// this test executes main.ts in a DOM and asserts the app rendered content.
afterEach(() => {
  document.body.innerHTML = '';
  vi.resetModules();
});

it('main.ts mounts the app into #app', async () => {
  document.body.innerHTML = '<div id="app"></div>';
  await import('./main.ts');
  const root = document.getElementById('app');
  expect(root).not.toBeNull();
  expect(root!.childElementCount).toBeGreaterThan(0);
});
