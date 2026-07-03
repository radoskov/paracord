// Embedding-cluster map renderer (D38 P3): papers placed in 2D by embedding proximity (server-side
// PCA-2D). Reuses the same node/x/y/size/color scatter shape as the temporal map, but the axes are
// the two fixed PCA components (no axis dropdowns) and color_group is the k-means cluster — one
// scatter series per cluster so ECharts renders a cluster legend. Hover shows the title + cluster;
// clicking a point opens the paper (handled by the page via the point's `name` = work id).

import type { VizPayload } from '../../api/client';
import { registerRenderer, type EChartsOptionLike, type VizRenderer } from './registry';
import { colorForGroup, type VizTheme } from './theme';

const MIN_SYMBOL = 8;
const MAX_SYMBOL = 38;
const DEFAULT_SYMBOL = 12;

// Nodes with real coordinates (a null on either axis means "unplaceable" → excluded).
function plottable(payload: VizPayload) {
  return payload.nodes.filter((n) => n.x !== null && n.y !== null);
}

// Map raw size values to a pixel radius so impact reads at a glance (mirrors the temporal map).
function symbolSizer(payload: VizPayload): (size: number | null) => number {
  const sizes = payload.nodes.map((n) => n.size).filter((s): s is number => s !== null);
  if (sizes.length === 0) return () => DEFAULT_SYMBOL;
  const min = Math.min(...sizes);
  const max = Math.max(...sizes);
  return (size) => {
    if (size === null) return DEFAULT_SYMBOL;
    if (max === min) return (MIN_SYMBOL + MAX_SYMBOL) / 2;
    return MIN_SYMBOL + ((size - min) / (max - min)) * (MAX_SYMBOL - MIN_SYMBOL);
  };
}

function tooltipFormatter(params: { data?: { node?: VizPayload['nodes'][number] } }): string {
  const node = params.data?.node;
  if (!node) return '';
  const m = node.meta ?? {};
  const cluster = m.cluster ?? node.color_group;
  const rows = [
    `<strong>${escapeHtml(node.label)}</strong>`,
    cluster != null ? `Cluster: ${escapeHtml(String(cluster))}` : '',
    m.year != null ? `Year: ${m.year}` : '',
  ].filter(Boolean);
  return rows.join('<br>');
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c,
  );
}

export const embeddingClusterRenderer: VizRenderer = {
  viewType: 'embedding_cluster',
  buildOption(payload: VizPayload, theme: VizTheme): EChartsOptionLike {
    const nodes = plottable(payload);
    const sizeFor = symbolSizer(payload);
    const groups = payload.legend?.groups ?? [];

    // One scatter series per cluster so ECharts renders a cluster legend; a single series when the
    // server reported no cluster groups.
    const seriesGroups = groups.length ? groups : [null];
    const series: Record<string, unknown>[] = seriesGroups.map((group) => {
      const groupNodes = group === null ? nodes : nodes.filter((n) => n.color_group === group);
      return {
        type: 'scatter',
        name: group ?? 'Papers',
        color: colorForGroup(theme, group, groups),
        data: groupNodes.map((n) => ({
          value: [n.x, n.y],
          name: n.id,
          node: n,
          symbolSize: sizeFor(n.size),
        })),
        emphasis: { focus: 'series' },
      };
    });

    return {
      backgroundColor: theme.background,
      textStyle: { color: theme.text, fontFamily: theme.fontFamily },
      legend: groups.length > 0 ? { top: 0, textStyle: { color: theme.text } } : undefined,
      tooltip: {
        trigger: 'item',
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBg,
        textStyle: { color: theme.tooltipText },
        formatter: tooltipFormatter,
      },
      grid: { left: 56, right: 24, top: groups.length > 0 ? 36 : 16, bottom: 48 },
      xAxis: {
        type: 'value',
        name: payload.axes?.x.label ?? 'Component 1',
        nameLocation: 'middle',
        nameGap: 28,
        scale: true,
        axisLine: { lineStyle: { color: theme.axisLine } },
        splitLine: { lineStyle: { color: theme.splitLine } },
      },
      yAxis: {
        type: 'value',
        name: payload.axes?.y.label ?? 'Component 2',
        nameLocation: 'middle',
        nameGap: 40,
        scale: true,
        axisLine: { lineStyle: { color: theme.axisLine } },
        splitLine: { lineStyle: { color: theme.splitLine } },
      },
      dataZoom: [
        { type: 'inside', xAxisIndex: 0 },
        { type: 'inside', yAxisIndex: 0 },
      ],
      series,
    };
  },
};

registerRenderer(embeddingClusterRenderer);
