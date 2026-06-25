import { mount } from 'svelte';

import App from './App.svelte';

// Svelte 5 mounts components with mount(); `new App({...})` (Svelte 4) throws at runtime.
const app = mount(App, {
  target: document.getElementById('app') as HTMLElement,
});

export default app;
