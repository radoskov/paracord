import { describe, expect, it } from "vitest";

import { hasRenderableMath, renderSummaryMath, wrapEgregiousMath } from "./renderMath";

describe("renderMath", () => {
  it("renders delimited inline math with KaTeX", () => {
    const html = renderSummaryMath("The bound is $O(n^2)$ overall.", { heuristic: false });
    expect(html).toContain("katex");
    expect(html).toContain("The bound is");
    expect(html).toContain("overall.");
  });

  it("escapes non-math HTML in the surrounding text", () => {
    const html = renderSummaryMath("a < b and c > d", { heuristic: false });
    expect(html).toContain("&lt;");
    expect(html).toContain("&gt;");
    expect(html).not.toContain("<b");
  });

  it("wraps egregious non-delimited math (\\frac, braced sup/subscript) but leaves easy tokens", () => {
    expect(wrapEgregiousMath("the term \\frac{a}{b} appears")).toContain("$\\frac{a}{b}$");
    expect(wrapEgregiousMath("we set D^{-1/2} here")).toContain("$");
    // Easy tokens are NOT wrapped.
    expect(wrapEgregiousMath("the vector N_i is small")).toBe("the vector N_i is small");
    expect(wrapEgregiousMath("complexity O(n) holds")).toBe("complexity O(n) holds");
  });

  it("does not touch text that already uses $ delimiters", () => {
    const t = "already $x^2$ delimited";
    expect(wrapEgregiousMath(t)).toBe(t);
  });

  it("detects renderable math", () => {
    expect(hasRenderableMath("plain prose only")).toBe(false);
    expect(hasRenderableMath("has $x$ math")).toBe(true);
    expect(hasRenderableMath("has \\frac{a}{b}")).toBe(true);
  });
});
