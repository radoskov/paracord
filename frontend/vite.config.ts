import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: {
    host: '127.0.0.1',
    port: 5173,
  },
  // Pre-bundle the heavy, lazily-imported libs (ECharts in the chart surfaces; pdfjs-dist in the
  // PDF reader; katex in the summary math renderer) so the dev server doesn't 504 with an "outdated
  // optimize dep" on their first import — which otherwise makes the app (or the reader/viz E2E
  // journeys) fail to load. katex is imported by lib/renderMath.ts, reached from main.ts, so a 504
  // on it blocks the whole app mount (2026-07-16).
  // NOTE: every entry must exist in package.json — an unresolvable include breaks the whole
  // dependency optimization pass (dead reader/charts), it doesn't just warn.
  optimizeDeps: {
    include: ['echarts', 'pdfjs-dist', 'katex'],
  },
});
