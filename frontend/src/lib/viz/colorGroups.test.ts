import { describe, expect, it } from "vitest";

import {
  distinctGroups,
  encodingRow,
  groupsOfViz,
  isHighlighted,
  nextChipState,
  orHiddenIds,
} from "./colorGroups";

describe("groupsOfViz", () => {
  it("prefers the multi-membership list, else boxes the single group, else empty", () => {
    expect(groupsOfViz({ color_groups: ["a", "b"], color_group: "a" })).toEqual(["a", "b"]);
    expect(groupsOfViz({ color_group: "solo" })).toEqual(["solo"]);
    expect(groupsOfViz({ color_groups: [], color_group: null })).toEqual([]);
    expect(groupsOfViz({})).toEqual([]);
  });
});

describe("distinctGroups", () => {
  it("dedupes and sorts alphabetically by default", () => {
    expect(distinctGroups([["b"], ["a", "b"], ["c"]])).toEqual(["a", "b", "c"]);
  });
  it("sorts years numerically with unknown last", () => {
    expect(distinctGroups([["2020"], ["unknown"], ["2018"]], "year")).toEqual([
      "2018",
      "2020",
      "unknown",
    ]);
  });
});

describe("orHiddenIds (OR semantics)", () => {
  const nodes = [
    { id: "multi", color_groups: ["x", "y", "z"] },
    { id: "single", color_group: "x" },
    { id: "uncolored" },
  ];
  const g = (n: (typeof nodes)[number]) => groupsOfViz(n);

  it("keeps a multi-color node while ANY of its colors is shown", () => {
    // x and y hidden, z still shown → multi stays visible.
    expect([...orHiddenIds(nodes, g, new Set(["x", "y"]))]).toEqual(["single"]);
  });
  it("hides a multi-color node only when ALL its colors are hidden", () => {
    expect([...orHiddenIds(nodes, g, new Set(["x", "y", "z"]))].sort()).toEqual(["multi", "single"]);
  });
  it("never hides an uncolored node, and hides nothing when the set is empty", () => {
    expect(orHiddenIds(nodes, g, new Set()).size).toBe(0);
    expect(orHiddenIds(nodes, g, new Set(["x", "y", "z"])).has("uncolored")).toBe(false);
  });
});

describe("isHighlighted", () => {
  it("highlights everything when nothing is hovered", () => {
    expect(isHighlighted(["a"], null)).toBe(true);
    expect(isHighlighted(["a"], new Set())).toBe(true);
  });
  it("highlights a node when ANY of its groups is hovered (OR)", () => {
    expect(isHighlighted(["a", "b"], new Set(["b"]))).toBe(true);
    expect(isHighlighted(["a", "b"], new Set(["c"]))).toBe(false);
  });
});

describe("encodingRow", () => {
  it("renders both halves, rounding whole values and showing small fractions to 4dp", () => {
    expect(
      encodingRow({ sizeLabel: "degree", sizeValue: 27, colorBy: "shelf", groups: ["kg", "emb"] }),
    ).toBe("size = degree: 27 · color = shelf: kg, emb");
    expect(encodingRow({ sizeLabel: "pagerank", sizeValue: 0.1234 })).toBe("size = pagerank: 0.1234");
  });
  it("drops a half whose inputs are missing", () => {
    expect(encodingRow({ colorBy: "tag", groups: ["t"] })).toBe("color = tag: t");
    expect(encodingRow({ sizeLabel: "x", sizeValue: null, colorBy: "tag", groups: [] })).toBe("");
  });
});

describe("nextChipState", () => {
  const all = ["a", "b", "c"];
  it("shift-click solos a group, then un-solos it", () => {
    const soloed = nextChipState("b", true, all, new Set(), null);
    expect([...soloed.hidden].sort()).toEqual(["a", "c"]);
    expect(soloed.solo).toBe("b");
    const unsoloed = nextChipState("b", true, all, soloed.hidden, soloed.solo);
    expect(unsoloed.hidden.size).toBe(0);
    expect(unsoloed.solo).toBeNull();
  });
  it("plain click toggles one group and clears any solo", () => {
    const hidden = nextChipState("a", false, all, new Set(), "b");
    expect([...hidden.hidden]).toEqual(["a"]);
    expect(hidden.solo).toBeNull();
    const shown = nextChipState("a", false, all, hidden.hidden, null);
    expect(shown.hidden.size).toBe(0);
  });
});
