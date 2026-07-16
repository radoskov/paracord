import { mount } from 'svelte';

import 'katex/dist/katex.min.css'; // 2026-07-16: bundled offline for summary math rendering
import App from './App.svelte';
import { initTheme } from './lib/theme/store';

// Boot the theme (localStorage cache → default) and inject its design tokens + set
// <html data-theme> BEFORE mounting, so scoped styles resolve against the token values on
// first paint. The inline script in index.html already set data-theme + a cached background
// before this module loaded, so there is no light→dark flash; the server value is reconciled
// once /auth/me returns (see App.svelte).
initTheme();

// Svelte 5 mounts components with mount(); `new App({...})` (Svelte 4) throws at runtime.
const app = mount(App, {
  target: document.getElementById('app') as HTMLElement,
});

export default app;
