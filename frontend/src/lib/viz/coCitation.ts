// Co-citation / bibliographic-coupling network renderer (D38 P5a). A node-link view with no fixed
// coordinates, so — consistent with the P2 decision to drive every view through ECharts rather than
// add a second (Cytoscape) rendering path — it uses an ECharts `graph` series with a force layout.
// Each scope paper is a node (symbol size = its co-citation/coupling degree, color = its color_group
// via one ECharts category per legend group); each payload edge is a link whose width encodes the
// shared-reference / shared-citer weight. Hover shows the paper; clicking a node opens the paper
// (the page reads the node's `name` = work id, exactly like the scatter views).

import type { VizPayload } from '../../api/client';
import { registerRenderer, type EChartsOptionLike, type VizRenderer } from './registry';
import { colorForGroup, type VizTheme } from './theme';

const MIN_SYMBOL = 10;
const MAX_SYMBOL = 44;
const DEFAULT_SYMBOL = 14;
const MIN_EDGE_WIDTH = 1;
const MAX_EDGE_WIDTH = 6;

/** Build a linear size-in-[MIN_SYMBOL, MAX_SYMBOL] mapper from the payload's node sizes;
 * a null size (or an all-null payload) falls back to DEFAULT_SYMBOL. */
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

/** Build a linear edge-width-in-[MIN_EDGE_WIDTH, MAX_EDGE_WIDTH] mapper from the payload's edge
 * weights (weight 1 is the minimum possible, so the scale starts there, not at 0). */
function edgeWidther(payload: VizPayload): (weight: number) => number {
  const weights = (payload.edges ?? []).map((e) => e.weight);
  if (weights.length === 0) return () => MIN_EDGE_WIDTH;
  const max = Math.max(...weights);
  return (weight) => {
    if (max <= 1) return MIN_EDGE_WIDTH;
    return MIN_EDGE_WIDTH + ((weight - 1) / (max - 1)) * (MAX_EDGE_WIDTH - MIN_EDGE_WIDTH);
  };
}

/** ECharts tooltip formatter: node hover shows title/year/degree, edge hover shows shared weight. */
function tooltipFormatter(params: {
  dataType?: string;
  data?: { node?: VizPayload['nodes'][number]; value?: number };
}): string {
  const node = params.data?.node;
  if (node) {
    const m = node.meta ?? {};
    const rows = [
      `<strong>${escapeHtml(node.label)}</strong>`,
      m.year != null ? `Year: ${m.year}` : '',
      m.degree != null ? `Linked papers: ${m.degree}` : '',
    ].filter(Boolean);
    return rows.join('<br>');
  }
  if (params.data?.value != null) return `Shared: ${params.data.value}`;
  return '';
}

/** Minimal HTML-escape for values interpolated into ECharts tooltip HTML. */
function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c,
  );
}

/** Renderer for the `co_citation` view: co-citation/bibliographic-coupling network as an ECharts
 * force-directed graph (see file header for the overall design). */
export const coCitationRenderer: VizRenderer = {
  viewType: 'co_citation',
  buildOption(payload: VizPayload, theme: VizTheme): EChartsOptionLike {
    const groups = payload.legend?.groups ?? [];
    const categories = groups.length
      ? groups.map((g, i) => ({ name: g, itemStyle: { color: colorForGroup(theme, g, groups) } }))
      : [{ name: 'Papers', itemStyle: { color: theme.categorical[0] } }];
    const sizeFor = symbolSizer(payload);
    const widthFor = edgeWidther(payload);

    const data = payload.nodes.map((n) => ({
      id: n.id,
      // `name` is the work id so the page's click handler opens the paper (as in the scatter views).
      name: n.id,
      symbolSize: sizeFor(n.size),
      category:
        groups.length && n.color_group ? Math.max(0, groups.indexOf(n.color_group)) : 0,
      node: n,
    }));
    const links = (payload.edges ?? []).map((e) => ({
      source: e.source,
      target: e.target,
      value: e.weight,
      lineStyle: { width: widthFor(e.weight) },
    }));

    return {
      backgroundColor: theme.background,
      textStyle: { color: theme.text, fontFamily: theme.fontFamily },
      legend:
        groups.length > 0 ? { top: 0, data: groups, textStyle: { color: theme.text } } : undefined,
      tooltip: {
        trigger: 'item',
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBg,
        textStyle: { color: theme.tooltipText },
        formatter: tooltipFormatter,
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          categories,
          data,
          links,
          force: { repulsion: 140, edgeLength: [40, 120], gravity: 0.08 },
          lineStyle: { color: theme.axisLine, opacity: 0.55, curveness: 0 },
          emphasis: { focus: 'adjacency', lineStyle: { width: MAX_EDGE_WIDTH } },
          label: { show: false },
        },
      ],
    };
  },
};

registerRenderer(coCitationRenderer);
