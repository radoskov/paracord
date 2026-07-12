// Global Vitest setup. The shared catalog store (lib/catalog.ts) is a module singleton, so its
// cached shelves/racks/tags would otherwise leak between tests — a component mounted in test B would
// see test A's data and skip its own listShelves() call. Reset it before every test so each starts
// with an empty, unloaded catalog (matching a fresh page load).
import { beforeEach } from 'vitest';

import { resetCatalog } from './lib/catalog';

beforeEach(() => {
  resetCatalog();
});
