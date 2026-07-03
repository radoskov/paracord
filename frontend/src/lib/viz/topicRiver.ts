// Topic-river renderer (D38 P5a): topic prevalence across publication years, drawn as a stacked-area
// streamgraph. Consumes the payload's typed `series` ({years, topics:[{label, values}]}) rather than
// nodes — one stacked line+area series per topic, colored from the shared categorical palette and
// legended by topic label. Each year's shares sum to 1 (a 100%-stacked prevalence view). Pure and
// synchronous so it is unit-testable in jsdom without importing echarts.

import type { VizPayload } from '../../api/client';
import { registerRenderer, type EChartsOptionLike, type VizRenderer } from './registry';
import type { VizTheme } from './theme';

function emptyOption(theme: VizTheme): EChartsOptionLike {
  return {
    backgroundColor: theme.background,
    textStyle: { color: theme.text, fontFamily: theme.fontFamily },
    series: [],
  };
}

export const topicRiverRenderer: VizRenderer = {
  viewType: 'topic_river',
  buildOption(payload: VizPayload, theme: VizTheme): EChartsOptionLike {
    const series = payload.series;
    if (!series || series.years.length === 0) return emptyOption(theme);

    const labels = series.topics.map((t) => t.label);
    const areaSeries = series.topics.map((topic, i) => ({
      type: 'line',
      name: topic.label,
      stack: 'total',
      areaStyle: { opacity: 0.7 },
      lineStyle: { width: 1 },
      showSymbol: false,
      smooth: false,
      color: theme.categorical[i % theme.categorical.length],
      emphasis: { focus: 'series' },
      data: topic.values,
    }));

    return {
      backgroundColor: theme.background,
      textStyle: { color: theme.text, fontFamily: theme.fontFamily },
      legend: labels.length > 0 ? { top: 0, data: labels, textStyle: { color: theme.text } } : undefined,
      tooltip: {
        trigger: 'axis',
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBg,
        textStyle: { color: theme.tooltipText },
        valueFormatter: (v: number) => `${Math.round(v * 100)}%`,
      },
      grid: { left: 52, right: 24, top: labels.length > 0 ? 36 : 16, bottom: 48 },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: series.years.map(String),
        name: 'Publication year',
        nameLocation: 'middle',
        nameGap: 30,
        axisLine: { lineStyle: { color: theme.axisLine } },
        axisLabel: { color: theme.text },
      },
      yAxis: {
        type: 'value',
        name: 'Topic share',
        min: 0,
        max: 1,
        axisLabel: { color: theme.text, formatter: (v: number) => `${Math.round(v * 100)}%` },
        axisLine: { lineStyle: { color: theme.axisLine } },
        splitLine: { lineStyle: { color: theme.splitLine } },
      },
      series: areaSeries,
    };
  },
};

registerRenderer(topicRiverRenderer);
