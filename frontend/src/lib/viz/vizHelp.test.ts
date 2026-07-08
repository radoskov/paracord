import { describe, expect, it } from "vitest";

import "./temporalMap";
import "./embeddingCluster";
import "./coCitation";
import "./topicRiver";
import "./similarityHeatmap";
import { registeredViewTypes } from "./registry";
import {
  AXIS_OPTION_HELP,
  VIEW_HELP,
  axisOptionHelp,
  helpForView,
} from "./vizHelp";

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

  it("has axis-option help for every temporal-map axis key", () => {
    for (const key of [
      "year",
      "citation_count",
      "local_degree",
      "citation_velocity",
      "similarity_to_focus",
      "topic_similarity_to_focus",
    ]) {
      expect(AXIS_OPTION_HELP[key], `missing axis help for ${key}`).toBeTruthy();
      expect(axisOptionHelp(key).length).toBeGreaterThan(10);
    }
  });

  it("returns a safe fallback for an unknown axis key", () => {
    expect(axisOptionHelp("mystery").length).toBeGreaterThan(0);
  });
});
