// Shared scope handling for the analysis tabs (Insights audit C3, 2026-07-14). Insights,
// Visualizations and Citation summary each kept their own copy of the scope state, readiness
// check and search→work-id resolution — and the copies had drifted (two tabs offered only 5 of
// the 7 scope types their backends accept). This module + ScopePicker.svelte is the one copy.
import type { ApiClient, GraphScopeType } from '../api/client';

/** The scope picker's raw state: the chosen type plus one id/query per id-carrying type. */
export interface ScopeSelection {
  scopeType: GraphScopeType;
  /** Shelf or rack id (for those two types). */
  scopeId: string;
  searchQuery: string;
  batchId: string;
  savedFilterId: string;
}

export function emptyScopeSelection(): ScopeSelection {
  return { scopeType: 'library', scopeId: '', searchQuery: '', batchId: '', savedFilterId: '' };
}

/** Whether the selection identifies a concrete set of papers (enables the build buttons). */
export function scopeSelectionReady(selection: ScopeSelection, selectedCount: number): boolean {
  switch (selection.scopeType) {
    case 'library':
      return true;
    case 'shelf':
    case 'rack':
      return !!selection.scopeId;
    case 'search_result':
      return !!selection.searchQuery.trim();
    case 'selected_papers':
      return selectedCount > 0;
    case 'import_batch':
      return !!selection.batchId;
    case 'saved_filter':
      return !!selection.savedFilterId;
    default:
      return false;
  }
}

/** The request-shaped scope arguments every analysis endpoint takes. */
export interface ScopeRequest {
  scopeType: GraphScopeType;
  scopeId?: string | null;
  workIds?: string[];
}

/**
 * Turn a picker selection into request arguments. ``search_result`` runs the metadata search now
 * and sends the resulting ids as the explicit work set; ``selected_papers`` sends the Library
 * tab's current multi-selection; the id-carrying types send their id as ``scopeId`` (the backend
 * expands a ``saved_filter`` id to its visibility-clamped work ids itself).
 */
export async function resolveScopeRequest(
  client: ApiClient,
  selection: ScopeSelection,
  selectedIds: readonly string[],
): Promise<ScopeRequest> {
  switch (selection.scopeType) {
    case 'search_result': {
      const works = (await client.listWorks({ q: selection.searchQuery, perPage: 500 })).items;
      return { scopeType: selection.scopeType, workIds: works.map((w) => w.id) };
    }
    case 'selected_papers':
      return { scopeType: selection.scopeType, workIds: [...selectedIds] };
    case 'import_batch':
      return { scopeType: selection.scopeType, scopeId: selection.batchId || null };
    case 'saved_filter':
      return { scopeType: selection.scopeType, scopeId: selection.savedFilterId || null };
    case 'shelf':
    case 'rack':
      return { scopeType: selection.scopeType, scopeId: selection.scopeId || null };
    default:
      return { scopeType: selection.scopeType, scopeId: null };
  }
}
