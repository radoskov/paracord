// Library table column registry + per-user column-preference persistence (localStorage).
//
// The backend prefs file is the durable source of truth (see client.getPreferences/putPreferences);
// localStorage is the instant, no-flash cache applied on mount before the backend reconciles.
//
// Columns are limited to what WorkRead actually returns per row. `has_pdf` is a filter (not a
// per-row field) so it is omitted; `file_count` (batch10) covers the "does it have files" need.
// `keywords`/`topics`/`badges`/`tags`/`shelves`/`racks` are opt-in extras (off by default).

import type { WorkSortKey } from '../api/client';

export type ColumnId =
  | 'title'
  | 'year'
  | 'venue'
  | 'status'
  | 'added_at'
  | 'doi'
  | 'arxiv_id'
  | 'keywords'
  | 'shelves'
  | 'racks'
  | 'file_count'
  | 'reference_count'
  | 'citation_count'
  | 'local_reference_count'
  | 'local_citation_count'
  | 'topics'
  | 'badges'
  | 'tags';

export interface ColumnDef {
  id: ColumnId;
  label: string;
  // Backend sort key this column maps to, when the column is sortable.
  sortKey?: WorkSortKey;
  // Shown by default in a fresh install.
  default: boolean;
  // Cannot be hidden (title anchors every row).
  alwaysOn?: boolean;
  // Default width RATIO (relative weight, not a unit): the rendered width is
  // ratio / sum(visible ratios) of the list's actual width, so widths adapt to whichever
  // columns are shown and however wide the list is. User-adjustable per column.
  width: number;
}

// The full registry, in the canonical (default) order.
export const LIBRARY_COLUMNS: ColumnDef[] = [
  { id: 'title', label: 'Title', sortKey: 'title', default: true, alwaysOn: true, width: 32 },
  { id: 'year', label: 'Year', sortKey: 'year', default: true, width: 7 },
  { id: 'venue', label: 'Venue', sortKey: 'venue', default: true, width: 14 },
  { id: 'status', label: 'Status', sortKey: 'reading_status', default: true, width: 9 },
  { id: 'added_at', label: 'Added', sortKey: 'added_at', default: true, width: 9 },
  { id: 'doi', label: 'DOI', default: true, width: 13 },
  { id: 'arxiv_id', label: 'arXiv ID', default: false, width: 10 },
  { id: 'keywords', label: 'Keywords', default: false, width: 16 },
  { id: 'shelves', label: 'Shelves', default: false, width: 14 },
  { id: 'racks', label: 'Racks', default: false, width: 14 },
  // batch10 columns — all opt-in (hidden by default so they don't push past the soft cap).
  { id: 'file_count', label: 'Files', sortKey: 'file_count', default: false, width: 5 },
  // batch12 reference/citation count columns — opt-in, all sortable server-side.
  {
    id: 'reference_count',
    label: 'References',
    sortKey: 'reference_count',
    default: false,
    width: 6,
  },
  { id: 'citation_count', label: 'Citations', sortKey: 'citation_count', default: false, width: 6 },
  {
    id: 'local_reference_count',
    label: 'Local refs',
    sortKey: 'local_reference_count',
    default: false,
    width: 6,
  },
  {
    id: 'local_citation_count',
    label: 'Local cites',
    sortKey: 'local_citation_count',
    default: false,
    width: 6,
  },
  { id: 'topics', label: 'Topics', default: false, width: 16 },
  { id: 'badges', label: 'Badges', default: false, width: 12 },
  { id: 'tags', label: 'Tags', default: false, width: 14 },
];

// User-adjustable width-ratio bounds (a 0/negative ratio would vanish a column; huge ones
// starve the rest).
export const MIN_COLUMN_WIDTH = 2;
export const MAX_COLUMN_WIDTH = 80;

// Soft cap on visible columns — a warning, not a hard limit (the user can override).
export const SOFT_COLUMN_CAP = 6;

export const ALL_COLUMN_IDS: ColumnId[] = LIBRARY_COLUMNS.map((c) => c.id);
const COLUMN_BY_ID = new Map<ColumnId, ColumnDef>(LIBRARY_COLUMNS.map((c) => [c.id, c]));
const ALWAYS_ON_IDS: ColumnId[] = LIBRARY_COLUMNS.filter((c) => c.alwaysOn).map((c) => c.id);

export const DEFAULT_ORDER: ColumnId[] = LIBRARY_COLUMNS.map((c) => c.id);
export const DEFAULT_VISIBLE: ColumnId[] = LIBRARY_COLUMNS.filter((c) => c.default).map((c) => c.id);

export interface ColumnSort {
  key: WorkSortKey;
  order: 'asc' | 'desc';
}

export interface ColumnPrefs {
  version: number;
  order: ColumnId[];
  visible: ColumnId[];
  sort: ColumnSort;
  // Per-column width ratios (relative weights; see ColumnDef.width). Always fully populated
  // after normalizeColumnPrefs.
  widths: Record<ColumnId, number>;
  // Show the thin divider lines between rows.
  dividers: boolean;
}

export const COLUMN_PREFS_VERSION = 1;
export const COLUMN_PREFS_STORAGE_KEY = 'paracord.library.columns';

const VALID_SORT_KEYS = new Set<string>(
  LIBRARY_COLUMNS.filter((c) => c.sortKey).map((c) => c.sortKey as string),
);

/** The registry's default width ratio for every column. */
export function defaultColumnWidths(): Record<ColumnId, number> {
  return Object.fromEntries(LIBRARY_COLUMNS.map((c) => [c.id, c.width])) as Record<
    ColumnId,
    number
  >;
}

/** The factory-default column prefs (order/visibility/sort/widths) for a fresh install. */
export function defaultColumnPrefs(): ColumnPrefs {
  return {
    version: COLUMN_PREFS_VERSION,
    order: [...DEFAULT_ORDER],
    visible: [...DEFAULT_VISIBLE],
    sort: { key: 'updated_at', order: 'desc' },
    widths: defaultColumnWidths(),
    dividers: true,
  };
}

function isColumnId(value: unknown): value is ColumnId {
  return typeof value === 'string' && COLUMN_BY_ID.has(value as ColumnId);
}

// Validate/repair an arbitrary blob against the registry. Drops unknown ids, dedupes, appends any
// registry columns missing from `order`, forces always-on columns visible, and clamps the sort key
// to a known sortable key. Returns a fully-formed ColumnPrefs that is always safe to render.
export function normalizeColumnPrefs(raw: unknown): ColumnPrefs {
  const base = defaultColumnPrefs();
  if (!raw || typeof raw !== 'object') return base;
  const input = raw as Partial<ColumnPrefs>;

  // Order: keep known ids in given order (deduped), then append any registry columns not listed so
  // a newly-added column still appears.
  const seen = new Set<ColumnId>();
  const order: ColumnId[] = [];
  for (const id of Array.isArray(input.order) ? input.order : []) {
    if (isColumnId(id) && !seen.has(id)) {
      seen.add(id);
      order.push(id);
    }
  }
  for (const id of DEFAULT_ORDER) {
    if (!seen.has(id)) {
      seen.add(id);
      order.push(id);
    }
  }

  // Visible: known ids only, restricted to those in `order`; always-on columns are forced on.
  const orderSet = new Set(order);
  const visibleSet = new Set<ColumnId>();
  for (const id of Array.isArray(input.visible) ? input.visible : DEFAULT_VISIBLE) {
    if (isColumnId(id) && orderSet.has(id)) visibleSet.add(id);
  }
  for (const id of ALWAYS_ON_IDS) visibleSet.add(id);
  // Preserve the order's sequence for the visible list.
  const visible = order.filter((id) => visibleSet.has(id));

  // Sort: known sortable key, valid direction; otherwise fall back to default.
  let sort: ColumnSort = base.sort;
  const rawSort = input.sort as Partial<ColumnSort> | undefined;
  if (rawSort && VALID_SORT_KEYS.has(String(rawSort.key))) {
    sort = {
      key: rawSort.key as WorkSortKey,
      order: rawSort.order === 'asc' ? 'asc' : 'desc',
    };
  }

  // Widths: known ids with finite, clamped ratios; anything missing/invalid falls back to the
  // registry default, so the map is always fully populated.
  const widths = defaultColumnWidths();
  const rawWidths = (input.widths ?? {}) as Record<string, unknown>;
  for (const [id, value] of Object.entries(rawWidths)) {
    const ratio = Number(value);
    if (isColumnId(id) && Number.isFinite(ratio)) {
      widths[id] = Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, Math.round(ratio)));
    }
  }

  const dividers = typeof input.dividers === 'boolean' ? input.dividers : true;

  return { version: COLUMN_PREFS_VERSION, order, visible, sort, widths, dividers };
}

/**
 * Percent width per visible column: ratio / sum(visible ratios) × 100 — the "column width
 * ratio" mechanic. The table renders these in a <colgroup> with table-layout: fixed, so widths
 * adapt to the list's actual width and to whichever column set is shown.
 */
export function columnWidthPercents(
  defs: ColumnDef[],
  widths: Record<ColumnId, number>,
): Record<ColumnId, number> {
  const fallback = defaultColumnWidths();
  const ratio = (id: ColumnId): number => widths[id] ?? fallback[id];
  const total = defs.reduce((sum, def) => sum + ratio(def.id), 0) || 1;
  return Object.fromEntries(
    defs.map((def) => [def.id, Math.round((ratio(def.id) / total) * 1000) / 10]),
  ) as Record<ColumnId, number>;
}

// True when the visible set exceeds the soft cap (caller surfaces a non-blocking warning).
export function exceedsSoftCap(prefs: ColumnPrefs): boolean {
  return prefs.visible.length > SOFT_COLUMN_CAP;
}

// Ordered list of visible ColumnDefs to render in the table.
export function visibleColumnDefs(prefs: ColumnPrefs): ColumnDef[] {
  return prefs.order
    .filter((id) => prefs.visible.includes(id))
    .map((id) => COLUMN_BY_ID.get(id))
    .filter((c): c is ColumnDef => !!c);
}

/** Read + validate the localStorage column-prefs cache; falls back to defaults on any error. */
export function loadColumnPrefs(): ColumnPrefs {
  try {
    const stored = localStorage.getItem(COLUMN_PREFS_STORAGE_KEY);
    if (!stored) return defaultColumnPrefs();
    return normalizeColumnPrefs(JSON.parse(stored));
  } catch {
    return defaultColumnPrefs();
  }
}

/** Persist column prefs to the localStorage cache (best-effort; see catch below). */
export function saveColumnPrefs(prefs: ColumnPrefs): void {
  try {
    localStorage.setItem(COLUMN_PREFS_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // localStorage may be unavailable (private mode / quota) — non-fatal; backend still persists.
  }
}
