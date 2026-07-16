// Shared reactive cache for the org-wide catalog entities: shelves, racks and tags. Every dropdown,
// picker and page that lists these subscribes to these stores instead of fetching its own private
// copy, so creating / renaming / deleting one anywhere updates every open dropdown live — no page
// reload (which would clear in-progress import widgets). Mirrors the cross-tab store pattern in
// lib/selection.ts.
import { get, writable } from 'svelte/store';

import type { ApiClient, Rack, Shelf, Tag } from '../api/client';

export const shelves = writable<Shelf[]>([]);
export const racks = writable<Rack[]>([]);
export const tags = writable<Tag[]>([]);

let shelvesLoaded = false;
let racksLoaded = false;
let tagsLoaded = false;
let shelvesInflight: Promise<Shelf[]> | null = null;
let racksInflight: Promise<Rack[]> | null = null;
let tagsInflight: Promise<Tag[]> | null = null;

// Force a re-fetch and publish to subscribers. Call after any mutation (create / rename / delete /
// membership change) so every subscribed dropdown reflects it immediately.
export async function refreshShelves(client: ApiClient): Promise<Shelf[]> {
  const list = await client.listShelves();
  shelves.set(list);
  shelvesLoaded = true;
  return list;
}

/** Force a re-fetch of racks and publish to subscribers; see {@link refreshShelves}. */
export async function refreshRacks(client: ApiClient): Promise<Rack[]> {
  const list = await client.listRacks();
  racks.set(list);
  racksLoaded = true;
  return list;
}

/** Force a re-fetch of tags and publish to subscribers; see {@link refreshShelves}. */
export async function refreshTags(client: ApiClient): Promise<Tag[]> {
  const list = await client.listTags();
  tags.set(list);
  tagsLoaded = true;
  return list;
}

// Fetch once, deduping concurrent callers, and reuse the cache on later mounts. Consumers subscribe
// to the store for live updates and call this on mount to prime it. Rejections propagate so callers
// can surface a load error; the cache stays unloaded so the next mount retries.
export async function ensureShelves(client: ApiClient): Promise<Shelf[]> {
  if (shelvesLoaded) return get(shelves);
  if (!shelvesInflight) {
    shelvesInflight = refreshShelves(client).finally(() => {
      shelvesInflight = null;
    });
  }
  return shelvesInflight;
}

/** Fetch racks once (deduped), reusing the cache on later mounts; see {@link ensureShelves}. */
export async function ensureRacks(client: ApiClient): Promise<Rack[]> {
  if (racksLoaded) return get(racks);
  if (!racksInflight) {
    racksInflight = refreshRacks(client).finally(() => {
      racksInflight = null;
    });
  }
  return racksInflight;
}

/** Fetch tags once (deduped), reusing the cache on later mounts; see {@link ensureShelves}. */
export async function ensureTags(client: ApiClient): Promise<Tag[]> {
  if (tagsLoaded) return get(tags);
  if (!tagsInflight) {
    tagsInflight = refreshTags(client).finally(() => {
      tagsInflight = null;
    });
  }
  return tagsInflight;
}

// Drop all cached catalog data (on logout / user switch) so the next ensure* refetches for the new
// session and stale, cross-user rows never leak into a dropdown.
export function resetCatalog(): void {
  shelves.set([]);
  racks.set([]);
  tags.set([]);
  shelvesLoaded = racksLoaded = tagsLoaded = false;
  shelvesInflight = racksInflight = tagsInflight = null;
}
