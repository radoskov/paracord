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

/** Render summary text to HTML with KaTeX for the math spans; non-math text is HTML-escaped. */
export function renderSummaryMath(text: string, { heuristic = true } = {}): string {
  const src = heuristic ? wrapEgregiousMath(text) : text;
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
  return text.includes("$") || EGREGIOUS.test(text);
}
