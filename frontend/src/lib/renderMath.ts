// 2026-07-16: render summary text with optional LaTeX ("fancy") math, or plain text (fallback).
//
// The LLM is prompted to delimit maths in $…$ (inline) or $$…$$ (display); we render those spans
// with bundled KaTeX. For summaries generated BEFORE that prompt change we also apply a LIGHT
// heuristic that wraps only the *egregious* non-delimited cases that read badly as plain text —
// backslash-commands (\frac, \sum, …) and braced sub/superscripts (D^{-1/2}, A_{ij}). Easy tokens
// like `N_i`, `O(n)` or already-unicode `O(N²)` are deliberately left alone.
import katex from "katex";

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// A run is "egregious" math if it contains a backslash-command or a BRACED super/subscript.
const EGREGIOUS = /(\\[a-zA-Z]+|[A-Za-z0-9)\]][_^]\{)/;
// A maximal math-ish token: letters/digits/operators/braces/backslashes — NO whitespace, so prose
// words never merge into one "token" (which would let a single \cmd swallow a whole sentence).
const MATHISH = /[A-Za-z0-9^_{}\\+\-*/=().,|~'’]+/g;

/** Best-effort: wrap egregious non-delimited math in $…$ so it renders. Conservative by design. */
export function wrapEgregiousMath(text: string): string {
  // Skip text that already has $ delimiters — assume it was authored with them.
  if (text.includes("$")) return text;
  return text.replace(MATHISH, (tok) => (EGREGIOUS.test(tok) ? `$${tok.trim()}$` : tok));
}

// Existing $…$/$$…$$ spans, so we can transform only the text OUTSIDE them (never corrupt authored
// math by re-wrapping parentheses that are part of an already-delimited expression).
const DOLLAR_SPAN = /\$\$[^$]+?\$\$|\$[^$\n]+?\$/g;

function transformOutsideDollar(text: string, fn: (run: string) => string): string {
  let out = "";
  let i = 0;
  let m: RegExpExecArray | null;
  DOLLAR_SPAN.lastIndex = 0;
  while ((m = DOLLAR_SPAN.exec(text)) !== null) {
    out += fn(text.slice(i, m.index)) + m[0];
    i = DOLLAR_SPAN.lastIndex;
  }
  return out + fn(text.slice(i));
}

/**
 * Normalize LaTeX the model delimited some OTHER way into $…$/$$…$$: the explicit \(…\) / \[…\]
 * delimiters, and bare (…) / […] spans that clearly hold a LaTeX command (a backslash-command
 * inside). Models sometimes wrap a whole equation in plain parentheses — e.g.
 * "(FM = \frac{1}{T}\sum_{q=1}^{T} …)" — which the token-level heuristic can't rescue because it
 * would only wrap the \frac…\sum fragment and leave the spaced remainder raw. Prose parentheticals
 * never contain a backslash-command, so requiring one keeps this low-risk. Applied only outside
 * existing $…$ spans so authored math is untouched.
 */
export function wrapDelimitedLatex(text: string): string {
  return transformOutsideDollar(text, (run) =>
    run
      .replace(/\\\[([\s\S]+?)\\\]/g, (_m, e) => `$$${e.trim()}$$`)
      .replace(/\\\(([\s\S]+?)\\\)/g, (_m, e) => `$${e.trim()}$`)
      .replace(/\(([^()\n]*\\[a-zA-Z][^()\n]*)\)/g, (_m, e) => `$${e.trim()}$`)
      .replace(/\[([^[\]\n]*\\[a-zA-Z][^[\]\n]*)\]/g, (_m, e) => `$${e.trim()}$`),
  );
}

/** Render summary text to HTML with KaTeX for the math spans; non-math text is HTML-escaped. */
export function renderSummaryMath(text: string, { heuristic = true } = {}): string {
  // First normalize other-delimited LaTeX (\(…\), bare parens with a command) to $…$; then apply the
  // token-level heuristic (which no-ops once any $ is present) for older, fully-undelimited summaries.
  const src = heuristic ? wrapEgregiousMath(wrapDelimitedLatex(text)) : text;
  let out = "";
  let i = 0;
  // Tokenize into $$…$$ (display), $…$ (inline), and plain runs.
  const re = /\$\$([^$]+?)\$\$|\$([^$\n]+?)\$/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    out += escapeHtml(src.slice(i, m.index));
    const display = m[1] != null;
    const expr = (m[1] ?? m[2] ?? "").trim();
    try {
      out += katex.renderToString(expr, { throwOnError: false, displayMode: display });
    } catch {
      out += escapeHtml(m[0]); // leave the raw $…$ if KaTeX can't parse it
    }
    i = re.lastIndex;
  }
  out += escapeHtml(src.slice(i));
  return out;
}

/** True if the text has anything worth rendering as math (delimited or egregious). */
export function hasRenderableMath(text: string): boolean {
  return text.includes("$") || text.includes("\\(") || text.includes("\\[") || EGREGIOUS.test(text);
}
