// Cross-tab selection: which paper/shelf/rack is "open", so switching tabs and coming back
// keeps the user's working context (e.g. open a shelf, pop to Library to find a paper, return).
import { writable } from 'svelte/store';

export const selectedWorkId = writable<string | null>(null);
export const selectedShelfId = writable<string | null>(null);
export const selectedRackId = writable<string | null>(null);

// The library multi-selection (ids of checked papers), mirrored from LibraryPage so other tabs
// (e.g. the Insights "Selected papers" graph scope) can operate on the current selection.
export const selectedPaperIds = writable<string[]>([]);

// A search to run in the Library tab, set from elsewhere (e.g. clicking a keyword chip in a paper's
// detail). LibraryPage consumes this once on mount/subscription and resets it to null.
export interface PendingSearch {
  query: string;
  mode: 'metadata' | 'semantic';
}

export const pendingLibrarySearch = writable<PendingSearch | null>(null);

// A paper to open in the Library tab, set from elsewhere (e.g. clicking a search result). LibraryPage
// subscribes, opens the paper (fetching it if not in the current list), and resets this to null.
export const pendingLibraryOpen = writable<string | null>(null);

// Text to append to the Import tab's "Batch import citations" box, set from elsewhere (e.g. clicking
// an external reference node in the reference graph, issue 5g). BatchImport subscribes, appends each
// non-empty payload as new line(s), and resets this to null.
export const pendingImportText = writable<string | null>(null);

// An arXiv id / DOI to prefill the Import tab's Identifier form with, set from elsewhere (e.g.
// clicking an external node in the Insights citation graph). ImportPage consumes it once --
// switches to the Identifier sub-tab and fills the input -- and resets this to null.
export const pendingIdentifierImport = writable<string | null>(null);
