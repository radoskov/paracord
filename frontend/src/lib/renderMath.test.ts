import { describe, expect, it } from "vitest";

import {
  hasRenderableMath,
  renderSummaryMath,
  wrapDelimitedLatex,
  wrapEgregiousMath,
} from "./renderMath";

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

  it("renders a whole equation the model wrapped in bare parentheses", () => {
    // The exact shape a model produced: a full equation in ( … ) with a \frac/\sum and spaces.
    const eq = "The metric (FM = \\frac{1}{T}\\sum_{q=1}^{T} M_T - M_0) is used.";
    const wrapped = wrapDelimitedLatex(eq);
    // The whole parenthesized span (spaces and all) becomes one $…$ expression, not just \frac…\sum.
    expect(wrapped).toContain("$FM = \\frac{1}{T}\\sum_{q=1}^{T} M_T - M_0$");
    const html = renderSummaryMath(eq);
    // Rendered by KaTeX (an <mfrac> is produced), not shown as the raw parenthesized LaTeX. (KaTeX
    // keeps the source TeX in a MathML <annotation>, so "\frac" legitimately appears there — we
    // assert on the rendered structure and that the raw "(FM =" text is gone instead.)
    expect(html).toContain("mfrac");
    expect(html).not.toContain("(FM =");
  });

  it("handles explicit \\(…\\) delimiters and leaves prose parentheticals alone", () => {
    expect(wrapDelimitedLatex("inline \\(a^2 + b^2\\) here")).toContain("$a^2 + b^2$");
    // A normal parenthetical (no backslash-command) is untouched.
    expect(wrapDelimitedLatex("see the results (Table 2) for details")).toBe(
      "see the results (Table 2) for details",
    );
  });

  it("does not corrupt parentheses inside already-$-delimited math", () => {
    const t = "the value $f(\\frac{a}{b})$ holds";
    expect(wrapDelimitedLatex(t)).toBe(t); // the $…$ span is preserved verbatim
  });

  it("detects renderable math", () => {
    expect(hasRenderableMath("plain prose only")).toBe(false);
    expect(hasRenderableMath("has $x$ math")).toBe(true);
    expect(hasRenderableMath("has \\frac{a}{b}")).toBe(true);
  });
});
