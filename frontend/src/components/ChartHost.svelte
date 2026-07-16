<!-- ChartHost — shared ECharts lifecycle wrapper (lazy-load, init, resize, dispose, theme repaint).
     Props: render (paint callback), onReady (one-time post-init hook), revision (bump to
     repaint), visible (resize-on-show), height, ariaLabel.
     Events/callbacks: none — callers drive repaints via `revision` and get the chart instance
     via `onReady`/`getChart()`.
     Non-obvious lifecycle/state: `paint()` fails silently into `failed` (no canvas-capable DOM,
     e.g. tests/SSR) so callers can show a list fallback via the `fallback` slot; resize is
     triggered both by a ResizeObserver on the container and by a visible-tab transition, since
     ECharts mis-measures inside `display:none`. -->
<script lang="ts">
  // Shared ECharts host (Insights audit M-a, 2026-07-14). Every chart surface previously
  // reimplemented the same lifecycle: lazy `import('echarts')`, init, ResizeObserver resize,
  // resize-on-tab-show, dispose on destroy, and re-render on theme change. This component owns
  // that lifecycle; callers only provide `render(chart)` (build options + setOption) and get the
  // instance back through `onReady` for one-time event wiring.
  import { onDestroy } from 'svelte';

  import { activeVizTheme } from '../lib/theme/store';

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type EChart = any;

  // Called whenever the chart should (re)paint: data change (bump `revision`), theme switch.
  export let render: (chart: EChart) => void | Promise<void>;
  // One-time hook after init — attach click handlers etc.
  export let onReady: ((chart: EChart) => void) | null = null;
  // Bump to request a re-render (e.g. new payload). Any change re-runs `render`.
  export let revision: unknown = 0;
  // Whether the enclosing tab is visible: ECharts mis-measures inside display:none, so we resize
  // when shown again.
  export let visible = true;
  export let height = 'min(60vh, 34rem)';
  export let ariaLabel = 'Chart';

  let container: HTMLDivElement | null = null;
  let chart: EChart = null;
  let failed = false;

  async function ensureChart(): Promise<EChart | null> {
    if (chart || !container) return chart;
    try {
      const echarts = (await import('echarts')) as unknown as {
        init: (el: HTMLElement) => EChart;
      };
      chart = echarts.init(container);
      observe(container);
      if (onReady) onReady(chart);
    } catch {
      failed = true; // no canvas-capable DOM (tests/SSR) — callers show their list fallback
    }
    return chart;
  }

  async function paint(): Promise<void> {
    const instance = await ensureChart();
    if (!instance) return;
    try {
      await render(instance);
      failed = false;
    } catch {
      failed = true;
    }
  }

  // Re-render on data revision or theme change (reading $activeVizTheme makes this reactive).
  $: if (container && revision !== undefined && $activeVizTheme) void paint();

  let wasVisible = true;
  $: {
    if (visible && !wasVisible && chart) chart.resize();
    wasVisible = visible;
  }

  let resizeObserver: ResizeObserver | null = null;
  function observe(el: HTMLElement): void {
    if (typeof ResizeObserver === 'undefined') return;
    if (resizeObserver) resizeObserver.disconnect();
    resizeObserver = new ResizeObserver(() => {
      if (chart) chart.resize();
    });
    resizeObserver.observe(el);
  }

  onDestroy(() => {
    if (resizeObserver) resizeObserver.disconnect();
    if (chart) chart.dispose();
  });

  export function getChart(): EChart {
    return chart;
  }

  export function getContainer(): HTMLDivElement | null {
    return container;
  }
</script>

<div class="chart-host" bind:this={container} role="img" aria-label={ariaLabel} style={`height:${height}`}></div>
{#if failed}
  <p class="chart-unavailable"><slot name="fallback">Interactive chart unavailable here.</slot></p>
{/if}

<style>
  .chart-host {
    background: var(--surface-raised);
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    width: 100%;
  }

  .chart-unavailable {
    color: var(--ink-muted);
    font-size: 0.85rem;
  }
</style>
