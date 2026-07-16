// Shift-click legend solo for ECharts legends (2026-07-14): shift-clicking a legend entry shows
// only that entry; shift-clicking the soloed entry again restores all. A plain click keeps the
// normal one-entry toggle. ECharts legend events carry no modifier keys, so the shift state is
// captured from the DOM click in the capture phase (runs before ECharts' own handler).
//
// Used by every chart with a native ECharts legend (reference graph, visualizations, …).
// CitationGraph renders its own legend chips (the native graph-series legend hover is broken)
// and implements the same gesture there.

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type EChart = any;

/**
 * Wire up shift-click legend-solo behavior on an already-created ECharts instance (see file header
 * for the gesture). Attaches a capture-phase DOM click listener to record the shift key (ECharts'
 * own `legendselectchanged` event carries no modifier info) plus a `legendselectchanged` handler
 * that re-dispatches `legendSelect`/`legendUnSelect` actions to enforce the solo state. Call once
 * per chart instance.
 */
export function enableLegendSolo(chart: EChart): void {
  let lastClickShift = false;
  let solo: string | null = null;
  let applying = false;
  chart
    .getDom()
    ?.addEventListener('click', (e: MouseEvent) => (lastClickShift = e.shiftKey), true);
  chart.on(
    'legendselectchanged',
    (params: { name: string; selected: Record<string, boolean> }) => {
      if (applying) return; // our own dispatched actions re-fire this event
      if (!lastClickShift) {
        solo = null; // a plain toggle breaks any solo state
        return;
      }
      applying = true;
      try {
        const restore = solo === params.name;
        for (const name of Object.keys(params.selected)) {
          chart.dispatchAction({
            type: restore || name === params.name ? 'legendSelect' : 'legendUnSelect',
            name,
          });
        }
        solo = restore ? null : params.name;
      } finally {
        applying = false;
      }
    },
  );
}
