// Cross-tab selection: which paper/shelf/rack is "open", so switching tabs and coming back
// keeps the user's working context (e.g. open a shelf, pop to Library to find a paper, return).
import { writable } from 'svelte/store';

export const selectedWorkId = writable<string | null>(null);
export const selectedShelfId = writable<string | null>(null);
export const selectedRackId = writable<string | null>(null);
