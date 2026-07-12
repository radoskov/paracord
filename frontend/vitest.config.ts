// Vitest config, kept separate from vite.config.ts so the dev/build path never imports
// test-only dependencies. Runs component tests in jsdom (executes the real Svelte mount),
// which is what catches client-render regressions like a wrong mount API.
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [svelte(), svelteTesting()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.ts'],
    setupFiles: ['src/test-setup.ts'],
  },
});
