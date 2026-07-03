// View registry (D38 P2): view_type -> renderer. Adding a view later (P3-P5) is one
// `registerRenderer` call — the page and API layer never change. Each renderer turns the shared
// VizPayload into an ECharts option object; the option is a plain object (not typed against
// echarts) so it stays lazy-loadable and unit-testable in jsdom without importing echarts.

import type { VizPayload } from '../../api/client';
import type { VizTheme } from './theme';

// A plain ECharts option object. Kept structural (not the echarts type) so this module never
// imports the heavy echarts bundle — the renderer is pure and the Svelte host lazy-loads echarts.
export type EChartsOptionLike = Record<string, unknown>;

export interface VizRenderer {
  viewType: string;
  // Build the ECharts option from a payload + theme. Pure and synchronous → unit-testable.
  buildOption(payload: VizPayload, theme: VizTheme): EChartsOptionLike;
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

/** All registered view types (for a view-type selector). */
export function registeredViewTypes(): string[] {
  return [...RENDERERS.keys()].sort();
}
