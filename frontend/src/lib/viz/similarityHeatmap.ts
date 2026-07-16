// Similarity-heatmap renderer (D38 P5a): a pairwise cosine-similarity matrix for a small selection,
// drawn as an ECharts `heatmap`. Consumes the payload's typed `matrix` ({labels, ids, values})
// rather than nodes; cells are [columnIndex, rowIndex, value] so the row/column order matches
// `labels`. A sequential visualMap ramp encodes similarity (diagonal is 1.0). Pure and synchronous
// so it is unit-testable in jsdom without importing echarts.

import type { VizPayload } from '../../api/client';
import { registerRenderer, type EChartsOptionLike, type VizRenderer } from './registry';
import type { VizTheme } from './theme';

/** Blank chart (no series) themed to match the rest of the viz, used when there's no matrix data. */
function emptyOption(theme: VizTheme): EChartsOptionLike {
  return {
    backgroundColor: theme.background,
    textStyle: { color: theme.text, fontFamily: theme.fontFamily },
    series: [],
  };
}

export const similarityHeatmapRenderer: VizRenderer = {
  viewType: 'similarity_heatmap',
  /**
   * Build the ECharts heatmap option from the payload's `matrix` ({labels, ids, values}).
   * Cells are emitted as [columnIndex, rowIndex, value] to match ECharts' heatmap data shape,
   * and the visualMap's min is clamped to the observed minimum (values may go negative) so the
   * ramp always spans the actual data.
   */
  buildOption(payload: VizPayload, theme: VizTheme): EChartsOptionLike {
    const matrix = payload.matrix;
    if (!matrix || matrix.labels.length === 0) return emptyOption(theme);

    const labels = matrix.labels;
    const cells: [number, number, number][] = [];
    let min = 1;
    for (let i = 0; i < matrix.values.length; i++) {
      for (let j = 0; j < matrix.values[i].length; j++) {
        const v = matrix.values[i][j];
        cells.push([j, i, v]);
        if (v < min) min = v;
      }
    }

    return {
      backgroundColor: theme.background,
      textStyle: { color: theme.text, fontFamily: theme.fontFamily },
      tooltip: {
        position: 'top',
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBg,
        textStyle: { color: theme.tooltipText },
        formatter: (params: { value?: [number, number, number] }) => {
          const value = params.value;
          if (!value) return '';
          const [col, row, sim] = value;
          return `${escapeHtml(labels[row])}<br>${escapeHtml(labels[col])}<br><strong>${sim.toFixed(3)}</strong>`;
        },
      },
      grid: { left: 120, right: 24, top: 24, bottom: 120 },
      xAxis: {
        type: 'category',
        data: labels,
        axisLabel: { color: theme.text, rotate: 60, interval: 0, width: 100, overflow: 'truncate' },
        axisLine: { lineStyle: { color: theme.axisLine } },
        splitArea: { show: true },
      },
      yAxis: {
        type: 'category',
        data: labels,
        axisLabel: { color: theme.text, width: 100, overflow: 'truncate' },
        axisLine: { lineStyle: { color: theme.axisLine } },
        splitArea: { show: true },
      },
      visualMap: {
        min: Math.min(0, min),
        max: 1,
        calculable: true,
        orient: 'vertical',
        right: 0,
        bottom: 'center',
        textStyle: { color: theme.text },
        inRange: { color: theme.sequential },
      },
      series: [
        {
          type: 'heatmap',
          name: 'Cosine similarity',
          data: cells,
          label: { show: false },
          emphasis: { itemStyle: { borderColor: theme.text, borderWidth: 1 } },
        },
      ],
    };
  },
};

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c,
  );
}

registerRenderer(similarityHeatmapRenderer);
