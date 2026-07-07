import { describe, expect, it } from "vitest";

import "./temporalMap";
import "./embeddingCluster";
import "./coCitation";
import "./topicRiver";
import "./similarityHeatmap";
import { registeredViewTypes } from "./registry";
import { VIEW_HELP, helpForView } from "./vizHelp";

describe("vizHelp (B1)", () => {
  it("has help content for every registered view type", () => {
    for (const vt of registeredViewTypes()) {
      const h = VIEW_HELP[vt];
      expect(h, `missing help for ${vt}`).toBeTruthy();
      expect(h.short.length).toBeGreaterThan(10);
      expect(h.about.length).toBeGreaterThan(10);
      expect(h.params.length).toBeGreaterThan(0);
    }
  });

  it("falls back safely for an unknown view type", () => {
    const h = helpForView("nope");
    expect(h.name).toBe("nope");
    expect(h.params.length).toBeGreaterThan(0);
  });
});
