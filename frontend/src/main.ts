import { mount } from 'svelte';

import App from './App.svelte';
import { applyTheme } from './lib/theme';

// Inject the design tokens and set <html data-theme> before mounting so scoped styles
// resolve against the token values on first paint.
applyTheme();

// Svelte 5 mounts components with mount(); `new App({...})` (Svelte 4) throws at runtime.
const app = mount(App, {
  target: document.getElementById('app') as HTMLElement,
});

export default app;
