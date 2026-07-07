import { describe, expect, it } from "vitest";

import type { VizPayload } from "../../api/client";
import { getRenderer, registeredViewTypes } from "./registry";
import { temporalMapRenderer } from "./temporalMap";
import { resolveTheme } from "./theme";

function makePayload(overrides: Partial<VizPayload> = {}): VizPayload {
  return {
    view_type: "temporal_map",
    axes: {
      x: { key: "year", label: "Publication year" },
      y: { key: "citation_count", label: "Citation count" },
    },
    axis_options: [
      { key: "year", label: "Publication year" },
      { key: "citation_count", label: "Citation count" },
    ],
    legend: { color_by: "status", groups: ["read", "unread"] },
    edges: null,
    notes: [],
    nodes: [
      {
        id: "a",
        x: 2020,
        y: 10,
        size: 2,
        color_group: "read",
        shape: "in_library",
        label: "A",
        meta: { year: 2020, citation_count: 10, local_degree: 2 },
      },
      {
        id: "b",
        x: 2018,
        y: 3,
        size: 1,
        color_group: "unread",
        shape: "in_library",
        label: "B",
        meta: { year: 2018, citation_count: 3, local_degree: 1 },
      },
      // Muted on the Y axis (null) → excluded from the plot.
      {
        id: "c",
        x: 2019,
        y: null,
        size: 0,
        color_group: "read",
        shape: "in_library",
        label: "C",
        meta: {},
      },
    ],
    ...overrides,
  };
}

describe("view registry", () => {
  it("registers the temporal_map renderer", () => {
    expect(registeredViewTypes()).toContain("temporal_map");
    expect(getRenderer("temporal_map")).toBe(temporalMapRenderer);
  });

  it("returns undefined for an unknown view type", () => {
    expect(getRenderer("nope")).toBeUndefined();
  });

  it("orders the temporal map first (it is the default visualization)", async () => {
    await import("./coCitation"); // register another renderer so ordering is observable
    expect(registeredViewTypes()[0]).toBe("temporal_map");
  });
});

describe("temporal_map renderer buildOption", () => {
  const theme = resolveTheme("light");

  it("maps the selected axes onto the ECharts axis names", () => {
    const option = temporalMapRenderer.buildOption(makePayload(), theme);
    expect((option.xAxis as { name: string }).name).toBe("Publication year");
    expect((option.yAxis as { name: string }).name).toBe("Citation count");
  });

  it("renders a year axis as whole years, not fractional/thousands-separated ticks", () => {
    const option = temporalMapRenderer.buildOption(makePayload(), theme);
    const x = option.xAxis as {
      minInterval?: number;
      axisLabel?: { formatter?: (v: number) => string };
    };
    expect(x.minInterval).toBe(1);
    expect(x.axisLabel?.formatter?.(2019)).toBe("2019"); // not "2,019"
    // The non-year axis (citation_count) keeps ECharts' default tick behaviour.
    expect(
      (option.yAxis as { minInterval?: number }).minInterval,
    ).toBeUndefined();
  });

  it("splits nodes into one scatter series per color group and excludes muted points", () => {
    const option = temporalMapRenderer.buildOption(makePayload(), theme);
    const series = option.series as Array<{
      type: string;
      name: string;
      data: unknown[];
    }>;
    const scatter = series.filter((s) => s.type === "scatter");
    expect(scatter.map((s) => s.name).sort()).toEqual(["read", "unread"]);
    // 'read' has a=(plottable) and c=(muted, excluded) → only 1 point; 'unread' has b → 1 point.
    const read = scatter.find((s) => s.name === "read");
    expect(read?.data).toHaveLength(1);
    expect((read?.data[0] as { name: string }).name).toBe("a");
  });

  it("encodes size onto per-point symbolSize", () => {
    const option = temporalMapRenderer.buildOption(makePayload(), theme);
    const series = option.series as Array<{
      name: string;
      data: Array<{ symbolSize: number }>;
    }>;
    const read = series.find((s) => s.name === "read");
    const unread = series.find((s) => s.name === "unread");
    // Larger raw size → larger symbol.
    expect(read?.data[0].symbolSize).toBeGreaterThan(
      unread?.data[0].symbolSize ?? 0,
    );
  });

  it("adds a lines series when the citation-edge overlay is present", () => {
    const payload = makePayload({
      edges: [{ source: "a", target: "b", weight: 1 }],
    });
    const option = temporalMapRenderer.buildOption(payload, theme);
    const series = option.series as Array<{ type: string; data: unknown[] }>;
    const lines = series.find((s) => s.type === "lines");
    expect(lines).toBeDefined();
    expect(lines?.data).toHaveLength(1);
  });

  it("produces a tooltip with the paper title and metadata", () => {
    const option = temporalMapRenderer.buildOption(makePayload(), theme);
    const formatter = (option.tooltip as { formatter: (p: unknown) => string })
      .formatter;
    const html = formatter({ data: { node: makePayload().nodes[0] } });
    expect(html).toContain("A");
    expect(html).toContain("2020");
    expect(html).toContain("Citations: 10");
  });

  it("uses a single series when color_by is none (no legend)", () => {
    const payload = makePayload({ legend: null });
    const option = temporalMapRenderer.buildOption(payload, theme);
    const series = option.series as Array<{ type: string; name: string }>;
    const scatter = series.filter((s) => s.type === "scatter");
    expect(scatter).toHaveLength(1);
    expect(scatter[0].name).toBe("Papers");
    expect(option.legend).toBeUndefined();
  });

  it("collapses overlapping papers into one count-badged marker with a members tooltip (B4)", () => {
    const payload = makePayload({
      legend: null,
      nodes: [
        {
          id: "a",
          x: 2020,
          y: 10,
          size: 1,
          color_group: null,
          shape: "in_library",
          label: "Paper A",
          meta: {},
        },
        {
          id: "b",
          x: 2020,
          y: 10,
          size: 2,
          color_group: null,
          shape: "in_library",
          label: "Paper B",
          meta: {},
        },
        {
          id: "c",
          x: 2019,
          y: 5,
          size: 1,
          color_group: null,
          shape: "in_library",
          label: "Paper C",
          meta: {},
        },
      ] as never,
    });
    const option = temporalMapRenderer.buildOption(payload, theme);
    const data = (
      option.series as Array<{
        data: Array<{
          name: string;
          members: { id: string }[];
          label?: { show: boolean; formatter: string };
        }>;
      }>
    )[0].data;
    // a + b share (2020,10) → one marker; c is separate → 2 markers total.
    expect(data).toHaveLength(2);
    const group = data.find((d) => d.members.length === 2)!;
    expect(group.label?.show).toBe(true);
    expect(group.label?.formatter).toBe("2");
    const html = (
      option.tooltip as { formatter: (p: unknown) => string }
    ).formatter({ data: group });
    expect(html).toContain("2 papers here");
    expect(html).toContain('data-viz-open="a"');
    expect(html).toContain('data-viz-open="b"');
  });
});
