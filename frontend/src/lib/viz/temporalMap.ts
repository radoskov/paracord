// Temporal citation map renderer (D38 P2): the Litmaps-style scatter. Each in-library paper is a
// point; both axes come from the payload's `axes` (independently selectable server-side). Encodings:
// size (from node.size), color (color_group split into one series per group so the legend works),
// shape (reserved — all temporal-map nodes are in-library works). An optional citation-edge overlay
// (payload.edges) is drawn as a lines series between node coordinates.

import type { VizPayload } from "../../api/client";
import {
  registerRenderer,
  type EChartsOptionLike,
  type VizRenderer,
} from "./registry";
import { colorForGroup, type VizTheme } from "./theme";

const MIN_SYMBOL = 8;
const MAX_SYMBOL = 38;
const DEFAULT_SYMBOL = 12;

// Explicit tick formatting per axis key (issue 5e): a year axis shows whole years (2019, 2020 — not
// ECharts' default fractional, thousands-separated 2,019 / 2,019.2); count-like axes round to
// integers; similarity/velocity axes show 2 decimals. Without this, several axes fall back to
// ECharts' defaults and can render blank or unhelpful ticks.
const _INTEGER_AXES = new Set(["citation_count", "local_degree"]);
const _DECIMAL_AXES = new Set([
  "citation_velocity",
  "similarity_to_focus",
  "topic_similarity_to_focus",
  "keyword_similarity_to_focus",
]);

function axisExtras(key: string | undefined): Record<string, unknown> {
  if (key === "year") {
    return {
      minInterval: 1,
      axisLabel: { formatter: (v: number) => String(Math.round(v)) },
    };
  }
  if (key && _INTEGER_AXES.has(key)) {
    return { axisLabel: { formatter: (v: number) => String(Math.round(v)) } };
  }
  if (key && _DECIMAL_AXES.has(key)) {
    return { axisLabel: { formatter: (v: number) => v.toFixed(2) } };
  }
  return {};
}

// Nodes plottable on both axes (a null on either axis means "unavailable" → excluded from the plot).
function plottable(payload: VizPayload) {
  return payload.nodes.filter((n) => n.x !== null && n.y !== null);
}

// Map raw size values to a pixel radius so small/large impact reads at a glance.
function symbolSizer(payload: VizPayload): (size: number | null) => number {
  const sizes = payload.nodes
    .map((n) => n.size)
    .filter((s): s is number => s !== null);
  if (sizes.length === 0) return () => DEFAULT_SYMBOL;
  const min = Math.min(...sizes);
  const max = Math.max(...sizes);
  return (size) => {
    if (size === null) return DEFAULT_SYMBOL;
    if (max === min) return (MIN_SYMBOL + MAX_SYMBOL) / 2;
    return (
      MIN_SYMBOL + ((size - min) / (max - min)) * (MAX_SYMBOL - MIN_SYMBOL)
    );
  };
}

type Node = VizPayload["nodes"][number];

// Group co-located papers (identical x,y) so an overlap becomes ONE count-badged marker instead of
// indistinguishable stacked dots. Grouping is within a color series (so a marker keeps one colour).
function groupByCoord(nodes: Node[]): Node[][] {
  const by = new Map<string, Node[]>();
  for (const n of nodes) {
    const key = `${n.x}|${n.y}`;
    const arr = by.get(key);
    if (arr) arr.push(n);
    else by.set(key, [n]);
  }
  return [...by.values()];
}

function singleTooltip(node: Node): string {
  const m = node.meta ?? {};
  return [
    `<strong>${escapeHtml(node.label)}</strong>`,
    m.year != null ? `Year: ${m.year}` : "",
    m.citation_count != null ? `Citations: ${m.citation_count}` : "",
    m.local_degree != null ? `Local degree: ${m.local_degree}` : "",
  ]
    .filter(Boolean)
    .join("<br>");
}

function tooltipFormatter(params: {
  data?: { node?: Node; members?: Node[] };
}): string {
  const members = params.data?.members;
  // Overlap: list every paper at this spot with an [open] link. The host delegates clicks on
  // [data-viz-open] to open the paper (the tooltip is enterable so the user can reach the links).
  if (members && members.length > 1) {
    const linkStyle =
      "color:inherit;text-decoration:underline;cursor:pointer;display:block;margin-top:2px";
    const rows = members
      .map(
        (m) =>
          `<a data-viz-open="${escapeHtml(m.id)}" style="${linkStyle}" title="Open this paper">${escapeHtml(m.label)}</a>`,
      )
      .join("");
    return `<strong>${members.length} papers here</strong>${rows}`;
  }
  const node = params.data?.node ?? members?.[0];
  return node ? singleTooltip(node) : "";
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] ?? c,
  );
}

export const temporalMapRenderer: VizRenderer = {
  viewType: "temporal_map",
  order: 0, // lead the view-type selector — the temporal map is the default visualization
  buildOption(payload: VizPayload, theme: VizTheme): EChartsOptionLike {
    const nodes = plottable(payload);
    const sizeFor = symbolSizer(payload);
    const groups = payload.legend?.groups ?? [];
    const colored = payload.legend !== null;

    // One scatter series per color group so ECharts renders a legend; a single series when
    // color_by is "none".
    const seriesGroups = colored ? (groups.length ? groups : [null]) : [null];
    const coordById = new Map<string, [number, number]>();
    for (const n of nodes) coordById.set(n.id, [n.x as number, n.y as number]);

    const series: Record<string, unknown>[] = seriesGroups.map((group) => {
      const groupNodes = colored
        ? nodes.filter((n) => n.color_group === group)
        : nodes;
      return {
        type: "scatter",
        name: group ?? "Papers",
        color: colorForGroup(theme, group, groups),
        // Merge exact overlaps into one marker; a >1 group shows a count badge and its tooltip
        // lists every paper there. `name` stays the representative id so a plain click still opens
        // a paper; the tooltip's [open] links reach the others.
        data: groupByCoord(groupNodes).map((members) => {
          const rep = members[0];
          return {
            value: [rep.x, rep.y],
            name: rep.id,
            node: rep,
            members,
            symbolSize: Math.max(...members.map((m) => sizeFor(m.size))),
            label:
              members.length > 1
                ? {
                    show: true,
                    formatter: String(members.length),
                    color: theme.tooltipText,
                    fontSize: 10,
                    fontWeight: "bold",
                  }
                : undefined,
          };
        }),
        emphasis: { focus: "series" },
      };
    });

    // Optional citation-edge overlay: a lines series connecting node coordinates.
    if (payload.edges && payload.edges.length > 0) {
      const lines = payload.edges
        .map((e) => ({
          coords: [coordById.get(e.source), coordById.get(e.target)],
        }))
        .filter((l) => l.coords[0] !== undefined && l.coords[1] !== undefined);
      if (lines.length > 0) {
        series.push({
          type: "lines",
          name: "Citations",
          coordinateSystem: "cartesian2d",
          data: lines,
          lineStyle: {
            color: theme.axisLine,
            opacity: 0.35,
            width: 1,
            curveness: 0,
          },
          silent: true,
          z: 0,
        });
      }
    }

    return {
      backgroundColor: theme.background,
      textStyle: { color: theme.text, fontFamily: theme.fontFamily },
      legend:
        colored && groups.length > 0
          ? { top: 0, textStyle: { color: theme.text } }
          : undefined,
      tooltip: {
        trigger: "item",
        // Enterable so the user can move into an overlap tooltip and click a specific paper's link.
        enterable: true,
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBg,
        textStyle: { color: theme.tooltipText },
        formatter: tooltipFormatter,
      },
      grid: {
        left: 56,
        right: 24,
        top: colored && groups.length > 0 ? 36 : 16,
        bottom: 48,
      },
      xAxis: {
        type: "value",
        name: payload.axes?.x.label ?? "",
        nameLocation: "middle",
        nameGap: 28,
        scale: true,
        ...axisExtras(payload.axes?.x.key),
        axisLine: { lineStyle: { color: theme.axisLine } },
        splitLine: { lineStyle: { color: theme.splitLine } },
      },
      yAxis: {
        type: "value",
        name: payload.axes?.y.label ?? "",
        nameLocation: "middle",
        nameGap: 40,
        scale: true,
        ...axisExtras(payload.axes?.y.key),
        axisLine: { lineStyle: { color: theme.axisLine } },
        splitLine: { lineStyle: { color: theme.splitLine } },
      },
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "inside", yAxisIndex: 0 },
      ],
      series,
    };
  },
};

registerRenderer(temporalMapRenderer);
