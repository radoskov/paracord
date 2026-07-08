import { describe, expect, it } from "vitest";

import type { ReferenceGraph } from "../../api/client";
import {
  DEFAULT_SECTION_WEIGHTS,
  buildReferenceGraphOption,
  weightedSize,
  yValueFor,
} from "./referenceGraph";
import { resolveTheme } from "./theme";

describe("referenceGraph weighting", () => {
  it("sums section counts × weights", () => {
    // methods ×4, intro ×2 → 2*4 + 1*2 = 10
    expect(
      weightedSize({ methods: 2, introduction: 1 }, DEFAULT_SECTION_WEIGHTS),
    ).toBe(10);
    expect(weightedSize({}, DEFAULT_SECTION_WEIGHTS)).toBe(0);
  });
});

describe("buildReferenceGraphOption", () => {
  const theme = resolveTheme("light");
  const graph: ReferenceGraph = {
    base_work_id: "base",
    nodes: [
      {
        id: "base",
        label: "Base",
        year: 2020,
        kind: "base",
        resolved_work_id: "base",
        section_counts: {},
        mention_count: 0,
        weighted: 0,
      },
      {
        id: "r1",
        label: "Local ref",
        year: 2015,
        kind: "local",
        resolved_work_id: "w1",
        section_counts: { methods: 2 },
        mention_count: 2,
        weighted: 8,
        citation_count: 42,
        local_degree: 3,
        topic_similarity: 0.5,
      },
      {
        id: "r2",
        label: "External ref",
        year: null,
        kind: "external",
        resolved_work_id: null,
        section_counts: { related: 1 },
        mention_count: 1,
        weighted: 1,
      },
    ],
    edges: [
      { source: "base", target: "r1" },
      { source: "base", target: "r2" },
    ],
  };

  it("builds one scatter series per kind + a citations lines series", () => {
    const option = buildReferenceGraphOption(
      graph,
      DEFAULT_SECTION_WEIGHTS,
      theme,
    );
    const series = option.series as Array<{
      type: string;
      name: string;
      data: unknown[];
    }>;
    const names = series.map((s) => s.name);
    expect(names).toContain("This paper");
    expect(names).toContain("In library");
    expect(names).toContain("External");
    expect(series.find((s) => s.type === "lines")?.name).toBe("Citations");
  });

  it('places a year-less external ref in the "no year" lane, left of the earliest year', () => {
    const option = buildReferenceGraphOption(
      graph,
      DEFAULT_SECTION_WEIGHTS,
      theme,
    );
    const external = (
      option.series as Array<{
        name: string;
        data: Array<{ value: [number, number]; node: { id: string } }>;
      }>
    ).find((s) => s.name === "External")!;
    const r2 = external.data.find((d) => d.node.id === "r2")!;
    expect(r2.value[0]).toBeLessThan(2015); // parked left of the earliest real year
    const fmt = (
      option.xAxis as { axisLabel: { formatter: (v: number) => string } }
    ).axisLabel.formatter;
    expect(fmt(r2.value[0])).toBe("no year");
    expect(fmt(2015)).toBe("2015");
  });

  it("yValueFor: weighted/mentions for all nodes, citations/topic/degree local-only", () => {
    const r1 = graph.nodes[1]; // local
    const r2 = graph.nodes[2]; // external
    expect(yValueFor(r1, "weighted", DEFAULT_SECTION_WEIGHTS)).toBe(8);
    expect(yValueFor(r1, "mentions", DEFAULT_SECTION_WEIGHTS)).toBe(2);
    expect(yValueFor(r1, "citations", DEFAULT_SECTION_WEIGHTS)).toBe(42);
    expect(yValueFor(r1, "topic", DEFAULT_SECTION_WEIGHTS)).toBe(0.5);
    expect(yValueFor(r1, "degree", DEFAULT_SECTION_WEIGHTS)).toBe(3);
    // External ref has no citation/topic/degree → null (→ the "n/a" lane).
    expect(yValueFor(r2, "citations", DEFAULT_SECTION_WEIGHTS)).toBeNull();
    expect(yValueFor(r2, "topic", DEFAULT_SECTION_WEIGHTS)).toBeNull();
    // ...but weighted/mentions still work for it.
    expect(yValueFor(r2, "weighted", DEFAULT_SECTION_WEIGHTS)).toBe(1);
  });

  it("routes n/a nodes to a dashed baseline lane below real values, and names the axis", () => {
    const option = buildReferenceGraphOption(
      graph,
      DEFAULT_SECTION_WEIGHTS,
      theme,
      {
        yAxis: "citations",
      },
    );
    const series = option.series as Array<{
      name: string;
      data: Array<{
        value: [number, number];
        itemStyle?: { borderType?: string };
      }>;
    }>;
    const local = series.find((s) => s.name === "In library")!.data[0];
    const external = series.find((s) => s.name === "External")!.data[0];
    expect(external.value[1]).toBeLessThan(local.value[1]); // parked on the baseline lane
    expect(external.itemStyle?.borderType).toBe("dashed");
    expect(local.itemStyle?.borderType).toBeUndefined(); // real value → solid
    expect((option.yAxis as { name: string }).name).toBe(
      "Global citation count",
    );
  });

  it("sizes the local ref larger than the external one (heavier weighted count)", () => {
    const option = buildReferenceGraphOption(
      graph,
      DEFAULT_SECTION_WEIGHTS,
      theme,
    );
    const series = option.series as Array<{
      name: string;
      data: Array<{ symbolSize: number }>;
    }>;
    const local = series.find((s) => s.name === "In library")!.data[0]
      .symbolSize;
    const external = series.find((s) => s.name === "External")!.data[0]
      .symbolSize;
    expect(local).toBeGreaterThan(external);
  });
});
