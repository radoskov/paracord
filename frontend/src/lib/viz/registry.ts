// View registry (D38 P2): view_type -> renderer. Adding a view later (P3-P5) is one
// `registerRenderer` call — the page and API layer never change. Each renderer turns the shared
// VizPayload into an ECharts option object; the option is a plain object (not typed against
// echarts) so it stays lazy-loadable and unit-testable in jsdom without importing echarts.

import type { VizPayload } from "../../api/client";
import type { VizTheme } from "./theme";

// A plain ECharts option object. Kept structural (not the echarts type) so this module never
// imports the heavy echarts bundle — the renderer is pure and the Svelte host lazy-loads echarts.
export type EChartsOptionLike = Record<string, unknown>;

// Per-render options the host passes down for the encoding info row and the legend-chip
// hover-highlight. Both are optional so a renderer/host that ignores them still works.
export interface RenderOpts {
  // Human label for the size encoding, shown in the tooltip's "size = …" info row (e.g. "degree",
  // "citation count", "weighted citations"). Omitted → the renderer skips/uses its own size label.
  sizeLabel?: string;
  // Legend-chip hover highlight: dim every node that has NONE of these color groups, so hovering a
  // chip makes ALL nodes carrying that color pop (OR semantics for multi-membership nodes). Null/
  // empty → no dimming.
  highlightGroups?: Set<string> | null;
}

export interface VizRenderer {
  viewType: string;
  // Ordering hint for the view-type selector (lower = earlier). Unset renderers sort after ordered
  // ones, then alphabetically. Lets us make the temporal map the default without a hardcoded string.
  order?: number;
  // Build the ECharts option from a payload + theme (+ host render options). Pure and synchronous →
  // unit-testable.
  buildOption(payload: VizPayload, theme: VizTheme, opts?: RenderOpts): EChartsOptionLike;
}

const RENDERERS = new Map<string, VizRenderer>();

/** Register a renderer for its view type. Later registration overrides an earlier one. */
export function registerRenderer(renderer: VizRenderer): void {
  RENDERERS.set(renderer.viewType, renderer);
}

/** Return the renderer for a view type, or undefined if none is registered. */
export function getRenderer(viewType: string): VizRenderer | undefined {
  return RENDERERS.get(viewType);
}

/** All registered view types (for a view-type selector), ordered by each renderer's `order` hint
 * (lower first), then alphabetically. So the temporal map (order 0) leads and is the page default. */
export function registeredViewTypes(): string[] {
  return [...RENDERERS.values()]
    .sort(
      (a, b) =>
        (a.order ?? 100) - (b.order ?? 100) ||
        a.viewType.localeCompare(b.viewType),
    )
    .map((r) => r.viewType);
}
