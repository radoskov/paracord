// Pure builder for the per-paper reference graph (B7): turns the /reference-graph payload + the
// user's section weights into an ECharts scatter option. Kept free of an echarts import so it is
import { pieSymbol } from "../graphPie";
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

// Blend a hex colour toward white by `amount` (0..1). Used to tint the "likely in library" colour
// as a lighter shade of the "in library" colour (#7) so it reads as related-but-unconfirmed.
function lighten(color: string, amount: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(color.trim());
  if (!m) return color;
  const num = parseInt(m[1], 16);
  const mix = (c: number) => Math.round(c + (255 - c) * amount);
  const r = mix((num >> 16) & 255);
  const g = mix((num >> 8) & 255);
  const b = mix(num & 255);
  return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
}

// Group co-located nodes (identical plotted x,y) so an overlap becomes ONE count-badged marker
// instead of indistinguishable stacked dots (ported from temporalMap.ts). Grouping is within one
// series so a marker keeps a single colour. Fixes ~100 citing papers pixel-stacking as "a handful".
// Never mixes click actions: nodes at one spot are also split by action kind (see actionKindOf).
function groupByCoord(
  nodes: ReferenceGraphNode[],
  xOf: (n: ReferenceGraphNode) => number | undefined,
  yOf: (n: ReferenceGraphNode) => number,
): ReferenceGraphNode[][] {
  const by = new Map<string, ReferenceGraphNode[]>();
  for (const n of nodes) {
    const key = `${xOf(n)}|${yOf(n)}|${actionKindOf(n)}`;
    const arr = by.get(key);
    if (arr) arr.push(n);
    else by.set(key, [n]);
  }
  return [...by.values()];
}

// What clicking a node does — the categories a mixed overlap must be split into. A cluster that
// mixed an in-library citing paper with external ones acted as its first member (usually
// "import"); splitting by action keeps every marker's click unambiguous.
type ActionKind = "base" | "local" | "likely" | "external";
const ACTION_ORDER: ActionKind[] = ["base", "local", "likely", "external"];

export function actionKindOf(n: ReferenceGraphNode): ActionKind {
  if (n.kind === "base") return "base";
  if (n.kind === "likely_local") return "likely";
  return n.resolved_work_id ? "local" : "external";
}

const ACTION_LABEL: Record<ActionKind, string> = {
  base: "this paper",
  local: "in library — click opens",
  likely: "likely in library — click reviews",
  external: "not in library — click imports",
};

// Horizontal fan-out (in year units) between the action-kind markers sharing one (x, y) spot, so
// e.g. the in-library and external citing papers of one year sit separately but near each other.
const OVERLAP_DX = 0.18;

// How many overlapping members a cluster tooltip lists before summarizing the rest ("…and N more").
const TOOLTIP_MEMBER_LIMIT = 200; // scrollable + filterable; capped so a huge cluster can't hang

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
    "Each node is a work this paper cites. It plots where those references sit in time and how heavily this paper leans on each, so you can see which references are load-bearing and how they cluster by year. " +
    "Interactions: click a node to open it (in library) or prefill the Import tab (external); ctrl-click a node OR a legend entry to show only it plus its direct neighbors (ctrl-click again or Reset view to undo); shift-click a legend entry to show only that kind/venue; scroll to zoom (near a chart edge the zoom pins itself to that data edge, handy for the bottom lanes). " +
    "Buttons: Show all re-fits the view, Reset view also clears legend filtering and the ctrl-click focus, Refresh refetches the data. The Max external limit is remembered per user.",
  xAxis:
    "The horizontal axis is always publication year (older to the left, newer to the right). References with no known year sit in a separate “no year” lane at the far left so they stay visible.",
  size: "Node size is always the section-weighted citation count — how heavily this paper relies on that reference. The per-section weights (abstract, methods, …) are set in your Profile.",
  color:
    "Colour marks the node kind: this paper, an in-library reference (a work you already have), a “likely in library” reference (a tolerant title match awaiting your confirmation, shown as a lighter tint of the in-library colour), or an external reference (cited but not in your library). When several nodes land on the same spot they collapse into one marker with a count badge — hover it to list them (up to 10, with the true total) and open or import each. Nodes with different click actions (in library / likely / external) never share a marker: they fan out side by side, so clicking always does what that marker's colour says.",
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

/**
 * Build the per-paper reference-graph ECharts scatter option from a fetched graph payload.
 * X is always publication year (a "no year" lane sits left of the earliest year); Y is the
 * selectable axis in `opts.yAxis` (default "weighted", see REFERENCE_Y_AXES); node size is always
 * the section-weighted mention count. Nodes are grouped by kind (or by venue when
 * `opts.colorBy === "venue"`) into separate scatter series so ECharts renders a legend; co-located
 * nodes collapse into one count-badged marker (see groupByCoord), and nodes sharing a plotted spot
 * but with different click actions are fanned out (see kindsAt/xPlot) so a click is unambiguous.
 * Reference/citing/ref-ref edges are added as separate `lines` series colour-coded by relation to
 * the base paper; ref-ref edges are included only when `opts.showRefEdges` is true.
 */
export function buildReferenceGraphOption(
  graph: ReferenceGraph,
  weights: Record<string, number>,
  theme: VizTheme,
  opts: { yAxis?: string; colorBy?: string; showRefEdges?: boolean } = {},
): EChartsOptionLike {
  const yAxisKey = opts.yAxis ?? "weighted";
  const colorBy = opts.colorBy ?? "kind";
  const showRefEdges = opts.showRefEdges ?? false;
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

  const citingColor = (theme.categorical ?? [])[4] ?? theme.text;

  const symbolFor = (n: ReferenceGraphNode) =>
    n.kind === "base" ? BASE_SYMBOL : sizeFor(weightedById.get(n.id) ?? 0);

  // When one (x, y) spot holds nodes with DIFFERENT click actions (in-library / likely / external),
  // fan them out horizontally by OVERLAP_DX so each action gets its own nearby marker instead of
  // one mixed cluster whose click acted as its first member. Computed over ALL nodes (the split
  // markers can live in different series). Offsets are fractional while raw x is whole years, so a
  // fanned marker can't land on another year's spot.
  const kindsAt = new Map<string, ActionKind[]>();
  for (const n of graph.nodes) {
    const key = `${x.get(n.id)}|${yFor(n)}`;
    const kinds = kindsAt.get(key) ?? [];
    const kind = actionKindOf(n);
    if (!kinds.includes(kind)) kinds.push(kind);
    kindsAt.set(key, kinds);
  }
  const xPlot = (n: ReferenceGraphNode): number => {
    const rawX = x.get(n.id) as number;
    const kinds = kindsAt.get(`${rawX}|${yFor(n)}`) ?? [];
    if (kinds.length < 2) return rawX;
    const ordered = [...kinds].sort(
      (a, b) => ACTION_ORDER.indexOf(a) - ACTION_ORDER.indexOf(b),
    );
    const i = ordered.indexOf(actionKindOf(n));
    return rawX + (i - (ordered.length - 1) / 2) * OVERLAP_DX;
  };

  // One plotted marker for a group of co-located nodes: a lone node renders as before; ≥2 nodes
  // collapse to a single count-badged marker whose tooltip lists every member (enterable links).
  const collapsedPoint = (members: ReferenceGraphNode[]) => {
    const rep = members[0];
    // Citing papers NOT in the library render as a lighter tint of the citing colour (one legend
    // entry — they're all citing papers — but in-library vs external stays distinguishable, the
    // same related-but-different scheme as "likely in library" vs "in library").
    const externalCiting = rep.kind === "citing" && !rep.resolved_work_id;
    const point: Record<string, unknown> = {
      value: [xPlot(rep), yFor(rep)],
      name: rep.id,
      node: rep,
      members,
      symbolSize: Math.max(...members.map(symbolFor)),
      // A node with no value for this axis gets a dashed outline so it reads as "n/a", not zero.
      ...(isNa(rep) || externalCiting
        ? {
            itemStyle: {
              ...(externalCiting ? { color: lighten(citingColor, 0.45) } : {}),
              ...(isNa(rep)
                ? {
                    borderType: "dashed",
                    borderColor: theme.text,
                    borderWidth: 1.5,
                    opacity: 0.65,
                  }
                : {}),
            },
          }
        : {}),
    };
    if (members.length > 1) {
      point.label = {
        show: true,
        formatter: String(members.length),
        color: theme.tooltipText,
        fontSize: 10,
        fontWeight: "bold",
      };
    }
    return point;
  };

  const grouped = (nodes: ReferenceGraphNode[]) =>
    groupByCoord(nodes, xPlot, yFor).map(collapsedPoint);

  const palette = theme.categorical ?? [];
  const localColor = palette[1] ?? theme.text;
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
      data: grouped(graph.nodes.filter((n) => venueOf(n) === v)),
      emphasis: { focus: "series" },
      z: 2,
    }));
  } else if (colorBy === "shelf" || colorBy === "rack" || colorBy === "tag") {
    // Membership coloring: one series (legend entry) per shelf/rack/tag name. Only the base +
    // in-library nodes carry memberships; everything else groups under "external / no data".
    // A node with SEVERAL memberships plots once (in its first group's series) but renders as a
    // color wheel with one segment per membership.
    const groupsOf = (n: ReferenceGraphNode) => n.memberships?.[colorBy] ?? [];
    const groups = [...new Set(graph.nodes.flatMap(groupsOf))].sort((a, b) => a.localeCompare(b));
    const colorOf = new Map(
      groups.map((g, i) => [g, palette[i % Math.max(1, palette.length)] ?? theme.text]),
    );
    const withPies = (points: Record<string, unknown>[]) =>
      points.map((point) => {
        const rep = point.node as ReferenceGraphNode;
        const repGroups = groupsOf(rep);
        return repGroups.length > 1
          ? { ...point, symbol: pieSymbol(repGroups.map((g) => colorOf.get(g) ?? theme.text)) }
          : point;
      });
    // Markers from DIFFERENT groups plot in different series, so co-located ones would stack —
    // nest a second fan-out (on top of the action-kind one) by the node's first group, mirroring
    // kindsAt/xPlot (2026-07-17 user report: pies hiding under single-color markers).
    const groupKeyOf = (n: ReferenceGraphNode): string => groupsOf(n)[0] ?? "__none__";
    const groupsAtSpot = new Map<string, string[]>();
    for (const n of graph.nodes) {
      const key = `${xPlot(n)}|${yFor(n)}`;
      const arr = groupsAtSpot.get(key) ?? [];
      if (!arr.includes(groupKeyOf(n))) arr.push(groupKeyOf(n));
      groupsAtSpot.set(key, arr);
    }
    const xPlotGrouped = (n: ReferenceGraphNode): number => {
      const base = xPlot(n);
      const at = groupsAtSpot.get(`${base}|${yFor(n)}`) ?? [];
      if (at.length < 2) return base;
      return base + (at.indexOf(groupKeyOf(n)) - (at.length - 1) / 2) * OVERLAP_DX;
    };
    const groupedM = (nodes: ReferenceGraphNode[]) =>
      groupByCoord(nodes, xPlotGrouped, yFor).map((members) => ({
        ...collapsedPoint(members),
        value: [xPlotGrouped(members[0]), yFor(members[0])],
      }));
    series = groups.map((g) => ({
      type: "scatter",
      name: g,
      color: colorOf.get(g),
      data: withPies(groupedM(graph.nodes.filter((n) => groupsOf(n)[0] === g))),
      emphasis: { focus: "series" },
      z: 2,
    }));
    const unmembered = graph.nodes.filter((n) => groupsOf(n).length === 0);
    if (unmembered.length) {
      series.push({
        type: "scatter",
        name: "external / no data",
        color: theme.splitLine,
        data: groupedM(unmembered),
        emphasis: { focus: "series" },
        z: 1,
      });
    }
  } else {
    // Default: one series per node kind so the legend reads local / likely / external / this paper.
    // Explicit palette indices per kind (was position-based, which made "base" and "external"
    // collide on palette[2]); base and external are kept far apart so the focal paper stands out.
    // "Likely in library" (#7) is a lighter tint of the "In library" colour — related but unconfirmed.
    const kinds: {
      key: "base" | "local" | "likely_local" | "external" | "citing";
      name: string;
      color: string;
      z: number;
    }[] = [
      { key: "base", name: "This paper", color: palette[0] ?? theme.axisLine, z: 3 },
      { key: "local", name: "In library", color: localColor, z: 2 },
      { key: "likely_local", name: "Likely in library", color: lighten(localColor, 0.45), z: 2 },
      { key: "external", name: "External", color: palette[3] ?? theme.splitLine, z: 2 },
      { key: "citing", name: "Cites this", color: citingColor, z: 2 },
    ];
    series = kinds.map((k) => ({
      type: "scatter",
      name: k.name,
      color: k.color,
      data: grouped(graph.nodes.filter((n) => n.kind === k.key)),
      emphasis: { focus: "series" },
      z: k.z,
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
  for (const n of graph.nodes) coordById.set(n.id, [xPlot(n), yFor(n)]);
  if (graph.edges.length) {
    // 2026-07-16: three distinct, fixed edge colours by relation to the base paper — reference
    // (base → cited work) in BLUE, citing (a paper → base) in GOLD, and ref↔ref (between local
    // references) in GREEN. Fixed hexes (not the categorical palette) so reference vs citing never
    // clash. Ref↔ref is shown only when the toggle is on (client-side — no refetch).
    const baseId = graph.nodes.find((n) => n.kind === "base")?.id;
    const REF_COLOR = "#3b82f6"; // blue
    const CITING_COLOR = "#d4a017"; // gold
    const REFREF_COLOR = "#2a9d8f"; // green/teal
    const edgeClasses = [
      {
        name: "Reference edges",
        color: REF_COLOR,
        test: (e: { source: string }) => e.source === baseId,
      },
      {
        name: "Citing edges",
        color: CITING_COLOR,
        test: (e: { target: string }) => e.target === baseId,
      },
      { name: "Ref↔ref edges", color: REFREF_COLOR, test: () => showRefEdges },
    ];
    const seen = new Set<string>();
    for (const cls of edgeClasses) {
      const data = graph.edges
        .filter((e) => {
          const key = `${e.source}|${e.target}`;
          if (seen.has(key) || !cls.test(e)) return false;
          seen.add(key);
          return true;
        })
        .map((e) => ({ coords: [coordById.get(e.source), coordById.get(e.target)] }))
        .filter((l) => l.coords[0] && l.coords[1]);
      if (!data.length) continue;
      series.push({
        type: "lines",
        name: cls.name,
        coordinateSystem: "cartesian2d",
        data,
        lineStyle: { color: cls.color, opacity: 0.5, width: 2, curveness: 0.1 },
        silent: true,
        z: 0,
      });
    }
  }

  return {
    backgroundColor: theme.background,
    textStyle: { color: theme.text, fontFamily: theme.fontFamily },
    legend: { top: 0, textStyle: { color: theme.text } },
    tooltip: {
      trigger: "item",
      // 5c: clamp the tooltip inside the chart box so a node near the modal edge doesn't overflow.
      confine: true,
      // Enterable so the user can move into an overlap tooltip and click a specific paper's link.
      enterable: true,
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBg,
      textStyle: { color: theme.tooltipText },
      formatter: (params: {
        data?: { node?: ReferenceGraphNode; members?: ReferenceGraphNode[] };
      }) => {
        const members = params.data?.members;
        if (members && members.length > 1) {
          // Overlap: list up to 10 members with an [open] link + the TRUE total, and an "import all"
          // affordance over the not-in-library members. The host delegates clicks on the links.
          const link =
            "color:inherit;text-decoration:underline;cursor:pointer;display:block;margin-top:2px";
          // 2026-07-16: show more members (up to a generous cap) in a SCROLLABLE box so "N more" is
          // revealed by scrolling within the enterable tooltip, not truncated away. External members
          // hint that ctrl/middle-click appends to the import box without switching tabs.
          const shown = members.slice(0, TOOLTIP_MEMBER_LIMIT);
          const rows = shown
            .map((m) => {
              const external = !m.resolved_work_id && m.kind !== "base";
              const title = external
                ? "Click to import (opens Import); ctrl/middle-click to add to the import box without switching"
                : "Open this reference";
              return `<a data-viz-open="${escapeHtml(m.id)}" style="${link}" title="${title}">${escapeHtml(m.label)}${m.year != null ? ` (${m.year})` : ""}</a>`;
            })
            .join("");
          const rowsBox = `<div data-viz-members style="max-height:220px;overflow-y:auto">${rows}</div>`;
          // 2026-07-16: a live case-insensitive filter over the listed papers (title + year), for
          // large clusters. Shown only when it helps; filtering is done in the host via a delegated
          // 'input' listener (CSP-safe — no inline handlers), hiding non-matching rows in place.
          const search =
            shown.length > 8
              ? `<input data-viz-search type="search" placeholder="filter ${members.length} papers…" style="width:100%;box-sizing:border-box;margin:4px 0;padding:2px 5px;font:inherit;border:1px solid ${theme.axisLine};border-radius:4px;background:${theme.tooltipBg};color:${theme.tooltipText}" />`
              : "";
          const more =
            members.length > shown.length
              ? `<div style="margin-top:2px;opacity:0.8">…and ${members.length - shown.length} more (narrow with a search)</div>`
              : "";
          const importable = members
            .filter((m) => !m.resolved_work_id && m.kind !== "base")
            .map((m) => m.id);
          const importAll = importable.length
            ? `<a data-viz-import-all="${escapeHtml(importable.join(","))}" style="${link};margin-top:6px;font-weight:bold" title="Prefill the import box with the papers here that aren't in your library">Import all ${importable.length} →</a>`
            : "";
          // Groups are split by action kind, so one label describes the whole cluster.
          return `<strong>${members.length} papers here · ${ACTION_LABEL[actionKindOf(members[0])]}</strong>${search}${rowsBox}${more}${importAll}`;
        }
        const n = params.data?.node ?? members?.[0];
        if (!n) return "";
        if (n.kind === "base")
          return `<strong>${escapeHtml(n.label)}</strong><br>(this paper)`;
        const kindLabel =
          n.kind === "local"
            ? "In library"
            : n.kind === "likely_local"
              ? `Likely in library${n.match_score != null ? ` · ${Math.round(n.match_score)}% match` : ""} — click to review`
              : n.kind === "citing"
                ? "Cites this paper"
                : "External";
        const breakdown = Object.entries(n.section_counts)
          .map(([b, c]) => `${b} ×${c}`)
          .join(", ");
        return [
          `<strong>${escapeHtml(n.label)}</strong>`,
          n.authors && n.authors.length ? escapeHtml(n.authors.join(", ")) : "",
          n.year != null ? `Year: ${n.year}` : "Year: n/a",
          kindLabel,
          n.kind === "citing"
            ? ""
            : `Cited ${n.mention_count}× ${breakdown ? `(${escapeHtml(breakdown)})` : ""}`,
          // Encoded channels (UX batch 3): what this node's position/size/colour mean.
          `<span style="opacity:.75">Y = ${escapeHtml(axisName)}: ${
            yById.get(n.id) != null ? String(yById.get(n.id)) : "n/a"
          } · size = weighted citations: ${weightedById.get(n.id) ?? "n/a"} · color = ${
            colorBy === "venue" ? `venue: ${escapeHtml(n.venue ?? "n/a")}` : `kind: ${n.kind}`
          }</span>`,
        ]
          .filter(Boolean)
          .join("<br>");
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
