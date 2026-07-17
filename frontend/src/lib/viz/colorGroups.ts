// Shared multi-color legend logic for the graph/scatter viz surfaces (2026-07-17). A node can carry
// SEVERAL color groups (a paper on several shelves/racks, or with several tags). The legend chips
// filter and highlight with OR semantics: a node stays visible while ANY of its colors is shown
// (hidden only when ALL are hidden), and highlights while ANY of its colors is hovered.
//
// The Insights citation/topic graph (CitationGraph.svelte) implements this inline; this module is
// the reusable core so the reference graph and the Visualizations-tab graphs behave identically.

// A node's color groups: the LIST (shelf/rack/tag) when present, else its single group boxed, else
// empty (uncolored — never hidden by color filtering).
export function groupsOfViz(n: {
  color_group?: string | null;
  color_groups?: string[] | null;
}): string[] {
  return n.color_groups?.length ? n.color_groups : n.color_group ? [n.color_group] : [];
}

// Distinct group names across the given per-node lists, sorted for a stable legend: years
// numerically (with "unknown" last), everything else alphabetically — matching CitationGraph.
export function distinctGroups(groupLists: string[][], colorBy?: string | null): string[] {
  const seen: string[] = [];
  for (const gs of groupLists) for (const g of gs) if (!seen.includes(g)) seen.push(g);
  if (colorBy === "year") {
    return seen.sort(
      (a, b) => (a === "unknown" ? 1 : 0) - (b === "unknown" ? 1 : 0) || Number(a) - Number(b),
    );
  }
  return seen.sort((a, b) => a.localeCompare(b));
}

// Ids of nodes hidden under OR semantics: hidden only when EVERY one of a node's color groups is in
// `hidden`. A node with no color group is never hidden here (color filtering doesn't apply to it).
export function orHiddenIds<T extends { id: string }>(
  nodes: T[],
  getGroups: (n: T) => string[],
  hidden: Set<string>,
): Set<string> {
  const out = new Set<string>();
  if (!hidden.size) return out;
  for (const n of nodes) {
    const gs = getGroups(n);
    if (gs.length && gs.every((g) => hidden.has(g))) out.add(n.id);
  }
  return out;
}

// The per-node encoding info row for a tooltip, e.g. "size = degree: 27 · color = shelf: kg, emb".
// Either half is dropped when its inputs are absent. Renderers wrap the result in their own dimmed
// span. Size values ≥1 (or 0) show as rounded integers; small fractions (pagerank etc.) show 4 dp.
export function encodingRow(opts: {
  sizeLabel?: string | null;
  sizeValue?: number | null;
  colorBy?: string | null;
  groups?: string[];
}): string {
  const parts: string[] = [];
  const v = opts.sizeValue;
  if (opts.sizeLabel && v != null && Number.isFinite(v)) {
    parts.push(`size = ${opts.sizeLabel}: ${Math.abs(v) >= 1 || v === 0 ? String(Math.round(v)) : v.toFixed(4)}`);
  }
  if (opts.colorBy && opts.groups && opts.groups.length) {
    parts.push(`color = ${opts.colorBy}: ${opts.groups.join(", ")}`);
  }
  return parts.join(" · ");
}

// True if a node (via its groups) should be highlighted for the currently hovered chip group(s):
// ANY of the node's groups is in `highlight`. An empty/absent highlight set means "no hover" →
// nothing is dimmed, so this returns true for every node.
export function isHighlighted(groups: string[], highlight: Set<string> | null | undefined): boolean {
  if (!highlight || !highlight.size) return true;
  return groups.some((g) => highlight.has(g));
}

// Next {hidden, solo} chip state for a click gesture — shift-click solos a group (toggling off an
// existing solo), a plain click toggles one group in/out of the hidden set. Mirrors CitationGraph's
// onChipClick so every surface shares one gesture model.
export function nextChipState(
  group: string,
  shiftKey: boolean,
  allGroups: string[],
  hidden: Set<string>,
  solo: string | null,
): { hidden: Set<string>; solo: string | null } {
  if (shiftKey) {
    if (solo === group) return { hidden: new Set(), solo: null };
    return { hidden: new Set(allGroups.filter((g) => g !== group)), solo: group };
  }
  const next = new Set(hidden);
  if (next.has(group)) next.delete(group);
  else next.add(group);
  return { hidden: next, solo: null };
}
