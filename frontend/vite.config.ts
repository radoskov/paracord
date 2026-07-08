import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: {
    host: '127.0.0.1',
    port: 5173,
  },
  // Pre-bundle the heavy, lazily-imported libs (chart/graph libs in the viz pages; pdfjs-dist in the
  // PDF reader) so the dev server doesn't 504 with an "outdated optimize dep" on their first dynamic
  // import — which otherwise makes the reader/viz E2E journeys flaky under a parallel run.
  optimizeDeps: {
    include: ['echarts', 'cytoscape', 'cytoscape-fcose', 'pdfjs-dist'],
  },
});
