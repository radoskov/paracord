// Pure builder for the per-paper reference graph (B7): turns the /reference-graph payload + the
// user's section weights into an ECharts scatter option. Kept free of an echarts import so it is
// unit-testable in jsdom (the modal lazy-loads echarts and calls setOption with this).
import type { ReferenceGraph, ReferenceGraphNode } from "../../api/client";
import type { EChartsOptionLike } from "./registry";
import type { VizTheme } from "./theme";

export const SECTION_BUCKETS = [
  "abstract",
  "introduction",
  "related",
  "methods",
  "results",
  "other",
] as const;

// Owner-approved defaults; editable per-user in Profile.
export const DEFAULT_SECTION_WEIGHTS: Record<string, number> = {
  abstract: 5,
  methods: 4,
  results: 3,
  introduction: 2,
  other: 2,
  related: 1,
};

/** Section-weighted mention count for one reference node. */
export function weightedSize(
  counts: Record<string, number>,
  weights: Record<string, number>,
): number {
  let sum = 0;
  for (const [bucket, n] of Object.entries(counts ?? {}))
    sum += (weights[bucket] ?? 1) * n;
  return sum;
}

const MIN_SYMBOL = 10;
const MAX_SYMBOL = 44;
const BASE_SYMBOL = 30;

// Map raw weighted values to a pixel radius (base paper gets a fixed prominent size).
function sizer(values: number[]): (v: number) => number {
  const positive = values.filter((v) => v > 0);
  const max = positive.length ? Math.max(...positive) : 0;
  return (v) => {
    if (v <= 0 || max === 0) return MIN_SYMBOL;
    return MIN_SYMBOL + (v / max) * (MAX_SYMBOL - MIN_SYMBOL);
  };
}

// X for a node: its year, or a "no year" lane placed one unit left of the earliest real year so
// year-less references stay visible instead of vanishing (design decision B7-Q3).
function xCoords(nodes: ReferenceGraphNode[]): {
  x: Map<string, number>;
  noYearX: number | null;
} {
  const years = nodes.map((n) => n.year).filter((y): y is number => y != null);
  const minYear = years.length
    ? Math.min(...years)
    : new Date().getUTCFullYear();
  const noYearX = nodes.some((n) => n.year == null) ? minYear - 2 : null;
  const x = new Map<string, number>();
  for (const n of nodes)
    x.set(n.id, n.year != null ? n.year : (noYearX as number));
  return { x, noYearX };
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] ?? c,
  );
}

export function buildReferenceGraphOption(
  graph: ReferenceGraph,
  weights: Record<string, number>,
  theme: VizTheme,
): EChartsOptionLike {
  const { x, noYearX } = xCoords(graph.nodes);
  const weightedById = new Map<string, number>();
  for (const n of graph.nodes) {
    weightedById.set(
      n.id,
      n.kind === "base" ? 0 : weightedSize(n.section_counts, weights),
    );
  }
  const sizeFor = sizer([...weightedById.values()]);

  // Y = section-weighted mention count (headline metric); base sits at the top for prominence.
  const maxWeighted = Math.max(1, ...[...weightedById.values()]);
  const yFor = (n: ReferenceGraphNode) =>
    n.kind === "base" ? maxWeighted * 1.15 : (weightedById.get(n.id) ?? 0);

  const point = (n: ReferenceGraphNode) => ({
    value: [x.get(n.id), yFor(n)],
    name: n.id,
    node: n,
    symbolSize:
      n.kind === "base" ? BASE_SYMBOL : sizeFor(weightedById.get(n.id) ?? 0),
  });

  // One series per node kind so the legend reads local / external / this paper, each its own colour.
  const kinds: {
    key: "base" | "local" | "external";
    name: string;
    color: string;
  }[] = [
    { key: "base", name: "This paper", color: theme.axisLine },
    { key: "local", name: "In library", color: theme.text },
    { key: "external", name: "External", color: theme.splitLine },
  ];
  const palette = theme.categorical ?? [];
  const series: Record<string, unknown>[] = kinds.map((k, i) => ({
    type: "scatter",
    name: k.name,
    color:
      k.key === "base"
        ? (palette[2] ?? k.color)
        : (palette[i % Math.max(1, palette.length)] ?? k.color),
    data: graph.nodes.filter((n) => n.kind === k.key).map(point),
    emphasis: { focus: "series" },
    z: k.key === "base" ? 3 : 2,
  }));

  const coordById = new Map<string, [number, number]>();
  for (const n of graph.nodes)
    coordById.set(n.id, [x.get(n.id) as number, yFor(n)]);
  if (graph.edges.length) {
    series.push({
      type: "lines",
      name: "Citations",
      coordinateSystem: "cartesian2d",
      data: graph.edges
        .map((e) => ({
          coords: [coordById.get(e.source), coordById.get(e.target)],
        }))
        .filter((l) => l.coords[0] && l.coords[1]),
      lineStyle: {
        color: theme.axisLine,
        opacity: 0.25,
        width: 1,
        curveness: 0.1,
      },
      silent: true,
      z: 0,
    });
  }

  return {
    backgroundColor: theme.background,
    textStyle: { color: theme.text, fontFamily: theme.fontFamily },
    legend: { top: 0, textStyle: { color: theme.text } },
    tooltip: {
      trigger: "item",
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBg,
      textStyle: { color: theme.tooltipText },
      formatter: (params: { data?: { node?: ReferenceGraphNode } }) => {
        const n = params.data?.node;
        if (!n) return "";
        if (n.kind === "base")
          return `<strong>${escapeHtml(n.label)}</strong><br>(this paper)`;
        const breakdown = Object.entries(n.section_counts)
          .map(([b, c]) => `${b} ×${c}`)
          .join(", ");
        return [
          `<strong>${escapeHtml(n.label)}</strong>`,
          n.year != null ? `Year: ${n.year}` : "Year: n/a",
          `${n.kind === "local" ? "In library" : "External"}`,
          `Cited ${n.mention_count}× ${breakdown ? `(${escapeHtml(breakdown)})` : ""}`,
        ].join("<br>");
      },
    },
    grid: { left: 56, right: 24, top: 32, bottom: 48 },
    xAxis: {
      type: "value",
      name: "Year",
      nameLocation: "middle",
      nameGap: 28,
      scale: true,
      minInterval: 1,
      axisLabel: {
        formatter: (v: number) =>
          noYearX != null && v === noYearX ? "no year" : String(Math.round(v)),
      },
      axisLine: { lineStyle: { color: theme.axisLine } },
      splitLine: { lineStyle: { color: theme.splitLine } },
    },
    yAxis: {
      type: "value",
      name: "Weighted citations",
      nameLocation: "middle",
      nameGap: 40,
      scale: true,
      axisLine: { lineStyle: { color: theme.axisLine } },
      splitLine: { lineStyle: { color: theme.splitLine } },
    },
    dataZoom: [
      { type: "inside", xAxisIndex: 0 },
      { type: "inside", yAxisIndex: 0 },
    ],
    series,
  };
}
