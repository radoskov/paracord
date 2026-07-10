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

export interface YAxisOption {
  key: string;
  label: string;
  axisName: string;
  /** What this axis plots and how to read it (shown in the graph's Help popup). */
  help: string;
}

// Selectable Y axes (B7 v2). X stays year; weighted mentions is the default. citations/topic/degree
// are local-only — external (or missing) nodes fall to the "n/a" lane.
export const REFERENCE_Y_AXES: YAxisOption[] = [
  {
    key: "weighted",
    label: "Weighted citations (default)",
    axisName: "Weighted citations",
    help: "How many times this paper cites the reference, weighted by where the citations appear (a mention in Methods counts more than one in Related work). The section weights are set in your Profile. This is also always the node size.",
  },
  {
    key: "mentions",
    label: "Mention count",
    axisName: "Times cited by this paper",
    help: "Raw number of times this paper cites the reference, ignoring section weighting. Available for every reference.",
  },
  {
    key: "citations",
    label: "Citation count",
    axisName: "Global citation count",
    help: "The reference's own citation count across all literature (from stored metadata). External references without metadata fall to the “n/a” lane.",
  },
  {
    key: "topic",
    label: "Topic similarity to this paper",
    axisName: "Topic similarity",
    help: "How strongly the reference shares this paper's topics. Only in-library references with topics have a value; the rest fall to the “n/a” lane.",
  },
  {
    key: "degree",
    label: "Local citation degree",
    axisName: "In-library citations",
    help: "How many papers in your library cite the reference — its connectedness within your collection. Only meaningful for in-library references; external ones fall to the “n/a” lane.",
  },
];

/** Help for the reference graph's settings beyond the Y-axis options (shown in the Help popup). */
export const REFERENCE_GRAPH_HELP = {
  overview:
    "Each node is a work this paper cites. It plots where those references sit in time and how heavily this paper leans on each, so you can see which references are load-bearing and how they cluster by year.",
  xAxis:
    "The horizontal axis is always publication year (older to the left, newer to the right). References with no known year sit in a separate “no year” lane at the far left so they stay visible.",
  size: "Node size is always the section-weighted citation count — how heavily this paper relies on that reference. The per-section weights (abstract, methods, …) are set in your Profile.",
  color:
    "Colour marks the node kind: this paper, an in-library reference (a work you already have), or an external reference (cited but not in your library).",
  refEdges:
    "“Local reference-to-reference edges”: when on, also draws citation links between the in-library references that cite one another — not just from this paper out to each reference — revealing how your cited works build on each other. Off by default because it needs those references' own extracted citations.",
  naLane:
    "References with no value for the chosen Y axis (e.g. an external reference with no citation count) sit on a dashed “n/a” lane below the data, marked with a line so they aren't misread as zero. A separate solid “0” line marks genuine zero values when any occur, so 0 and “no data” stay distinct.",
} as const;

/** The Y value for a node under the chosen axis, or null when it has none (→ the "n/a" lane). */
export function yValueFor(
  node: ReferenceGraphNode,
  yAxis: string,
  weights: Record<string, number>,
): number | null {
  switch (yAxis) {
    case "mentions":
      return node.mention_count ?? 0;
    case "citations":
      return node.citation_count ?? null;
    case "topic":
      return node.topic_similarity ?? null;
    case "degree":
      return node.local_degree ?? null;
    default:
      return weightedSize(node.section_counts, weights);
  }
}

export function buildReferenceGraphOption(
  graph: ReferenceGraph,
  weights: Record<string, number>,
  theme: VizTheme,
  opts: { yAxis?: string; colorBy?: string } = {},
): EChartsOptionLike {
  const yAxisKey = opts.yAxis ?? "weighted";
  const colorBy = opts.colorBy ?? "kind";
  const axisName =
    REFERENCE_Y_AXES.find((o) => o.key === yAxisKey)?.axisName ??
    "Weighted citations";
  const { x, noYearX } = xCoords(graph.nodes);
  const weightedById = new Map<string, number>();
  for (const n of graph.nodes) {
    weightedById.set(
      n.id,
      n.kind === "base" ? 0 : weightedSize(n.section_counts, weights),
    );
  }
  const sizeFor = sizer([...weightedById.values()]);

  // Node SIZE is always the weighted mention count ("how heavily cited"); the Y axis is selectable.
  const refNodes = graph.nodes.filter((n) => n.kind !== "base");
  const yById = new Map<string, number | null>();
  for (const n of refNodes) yById.set(n.id, yValueFor(n, yAxisKey, weights));
  const real = [...yById.values()].filter((v): v is number => v != null);
  const maxY = real.length ? Math.max(...real) : 1;
  const minY = real.length ? Math.min(...real) : 0;
  const span = Math.max(1, maxY - minY);
  const naY = minY - span * 0.18 - 0.5; // baseline "n/a" lane, below every real value
  const baseY = maxY + span * 0.15 + 0.5; // base paper pinned above the data

  const isNa = (n: ReferenceGraphNode) =>
    n.kind !== "base" && yById.get(n.id) == null;
  const hasNa = refNodes.some(isNa);
  const yFor = (n: ReferenceGraphNode) =>
    n.kind === "base" ? baseY : (yById.get(n.id) ?? naY);

  const point = (n: ReferenceGraphNode) => ({
    value: [x.get(n.id), yFor(n)],
    name: n.id,
    node: n,
    symbolSize:
      n.kind === "base" ? BASE_SYMBOL : sizeFor(weightedById.get(n.id) ?? 0),
    // A node with no value for this axis gets a dashed outline so it reads as "n/a", not zero.
    ...(isNa(n)
      ? {
          itemStyle: {
            borderType: "dashed",
            borderColor: theme.text,
            borderWidth: 1.5,
            opacity: 0.65,
          },
        }
      : {}),
  });

  const palette = theme.categorical ?? [];
  let series: Record<string, unknown>[];
  if (colorBy === "venue") {
    // 5d: one series (colour) per distinct venue. External refs without a venue group under
    // "unknown". Node shape/size are unchanged; only the colour grouping differs.
    const venueOf = (n: ReferenceGraphNode) => n.venue || "unknown";
    const venues = [...new Set(graph.nodes.map(venueOf))];
    series = venues.map((v, i) => ({
      type: "scatter",
      name: v,
      color: palette[i % Math.max(1, palette.length)] ?? theme.text,
      data: graph.nodes.filter((n) => venueOf(n) === v).map(point),
      emphasis: { focus: "series" },
      z: 2,
    }));
  } else {
    // Default: one series per node kind so the legend reads local / external / this paper.
    // Explicit palette indices per kind (was position-based, which made "base" and "external"
    // collide on palette[2]); base and external are kept far apart so the focal paper stands out.
    const kinds: {
      key: "base" | "local" | "external" | "citing";
      name: string;
      idx: number;
      fallback: string;
    }[] = [
      { key: "base", name: "This paper", idx: 0, fallback: theme.axisLine },
      { key: "local", name: "In library", idx: 1, fallback: theme.text },
      { key: "external", name: "External", idx: 3, fallback: theme.splitLine },
      { key: "citing", name: "Cites this", idx: 4, fallback: theme.text },
    ];
    series = kinds.map((k) => ({
      type: "scatter",
      name: k.name,
      color: palette[k.idx % Math.max(1, palette.length)] ?? k.fallback,
      data: graph.nodes.filter((n) => n.kind === k.key).map(point),
      emphasis: { focus: "series" },
      z: k.key === "base" ? 3 : 2,
    }));
  }

  // 5a: mark the special lanes with a persistent thick line + label so they can't be misread. A
  // "0" line whenever any real node actually sits at zero on this axis, and an "n/a" line for the
  // missing-value lane — either or both may show. Attached to the first series so it renders once
  // without adding a legend entry.
  const markLineData: Record<string, unknown>[] = [];
  if (real.includes(0)) {
    markLineData.push({ name: "0", yAxis: 0, lineStyle: { type: "solid" } });
  }
  if (hasNa) {
    markLineData.push({ name: "n/a", yAxis: naY, lineStyle: { type: "dashed" } });
  }
  if (series.length && markLineData.length) {
    series[0].markLine = {
      symbol: "none",
      silent: true,
      lineStyle: { color: theme.text, width: 2, opacity: 0.7 },
      label: { show: true, position: "end", color: theme.text, formatter: "{b}" },
      data: markLineData,
    };
  }

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
      // 5c: clamp the tooltip inside the chart box so a node near the modal edge doesn't overflow.
      confine: true,
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
      name: axisName,
      nameLocation: "middle",
      nameGap: 40,
      scale: true,
      axisLine: { lineStyle: { color: theme.axisLine } },
      splitLine: { lineStyle: { color: theme.splitLine } },
      // 5e: always format Y ticks (not only when there's an n/a lane): count-like axes round to
      // integers, weighted/topic show up to 2 decimals, and the n/a-lane tick is labelled "n/a".
      axisLabel: {
        formatter: (v: number) => {
          if (hasNa && Math.abs(v - naY) < 1e-9) return "n/a";
          if (["mentions", "citations", "degree"].includes(yAxisKey)) {
            return String(Math.round(v));
          }
          return Number.isInteger(v) ? String(v) : v.toFixed(2);
        },
      },
    },
    dataZoom: [
      { type: "inside", xAxisIndex: 0 },
      { type: "inside", yAxisIndex: 0 },
    ],
    series,
  };
}
