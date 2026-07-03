import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: {
    host: '127.0.0.1',
    port: 5173,
  },
  // Pre-bundle the heavy chart/graph libs (lazily imported in the viz pages) so the dev server
  // doesn't 504 with an "outdated optimize dep" on their first dynamic import.
  optimizeDeps: {
    include: ['echarts', 'cytoscape', 'cytoscape-fcose'],
  },
});
