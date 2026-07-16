// Categorical data-palette validator — a compact port of the dataviz skill's
// `validate_palette.js` (the checks that are computable from colour alone). Used by
// the theme tests to gate every bundled theme's `graph.categorical` palette, and
// available for P4's user-theme validation. See docs/THEMING_DESIGN.md.
//
// Four checks: OKLCH lightness band, OKLCH chroma floor, Machado-2009 CVD ΔE
// (adjacent protan/deutan), and WCAG contrast vs the chart surface. A CVD result in
// the 8–12 floor band is a WARN, not a FAIL (legal because the graph/network views
// ship a legend + labels — the required secondary encoding).

const BAND: Record<'light' | 'dark', [number, number]> = {
  light: [0.43, 0.77],
  dark: [0.48, 0.67],
};
const CHROMA_FLOOR = 0.1;
const CVD_TARGET = 12.0;
const CVD_FLOOR = 8.0;
const CONTRAST_MIN = 3.0;

// Machado, Oliveira & Fernandes (2009) full-severity dichromacy simulation matrices
// (linear-RGB -> simulated linear-RGB), for protanopia and deuteranopia.
const MACHADO = {
  protan: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deutan: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
} as const;

/** Parse a `#rrggbb` hex string into 0–1 sRGB components. */
function hex2srgb(h: string): [number, number, number] {
  const s = h.trim().replace(/^#/, '');
  return [0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16) / 255) as [number, number, number];
}
// sRGB electro-optical transfer function (gamma decode) for a single channel.
const s2lin = (c: number): number => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
/** Hex -> linear-light RGB (0–1), i.e. gamma-decoded sRGB. */
const lin = (h: string): [number, number, number] =>
  hex2srgb(h).map(s2lin) as [number, number, number];
/** WCAG relative luminance (linear RGB, Rec. 709 weights) of a hex colour. */
const relLum = (h: string): number => {
  const [r, g, b] = lin(h);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

/** WCAG contrast ratio between two hex colours (order-independent, >= 1). */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [relLum(a), relLum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Hex -> OKLCH [lightness, chroma] (hue is unused by the checks so it's dropped). */
function oklch(h: string): [number, number] {
  const [r, g, b] = lin(h);
  // Linear RGB -> LMS cone response (OKLab matrix) ...
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  // ... then LMS -> OKLab L/a/b.
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return [L, Math.hypot(A, B)]; // chroma = hypot(a, b)
}

/** Linear-light sRGB -> CIELAB (D65 white point), used as the ΔE space for CVD comparisons. */
function lin2lab(r: number, g: number, b: number): [number, number, number] {
  const X = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b;
  const Y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b;
  const Z = 0.0193339 * r + 0.119192 * g + 0.9503041 * b;
  const f = (t: number): number => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const [fx, fy, fz] = [f(X / 0.95047), f(Y / 1.0), f(Z / 1.08883)];
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}
/** Apply the Machado-2009 dichromacy simulation matrix for `kind` to a hex colour. */
function simulate(h: string, kind: 'protan' | 'deutan'): [number, number, number] {
  const [r, g, b] = lin(h);
  const M = MACHADO[kind];
  const clamp = (c: number): number => Math.max(0, Math.min(1, c));
  return [
    clamp(M[0][0] * r + M[0][1] * g + M[0][2] * b),
    clamp(M[1][0] * r + M[1][1] * g + M[1][2] * b),
    clamp(M[2][0] * r + M[2][1] * g + M[2][2] * b),
  ];
}
/** CIE76 ΔE between two colours as perceived under a simulated CVD `kind`. */
function deltaE(h1: string, h2: string, kind: 'protan' | 'deutan'): number {
  const a = lin2lab(...simulate(h1, kind));
  const b = lin2lab(...simulate(h2, kind));
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

export interface CategoricalReport {
  ok: boolean;
  cvdState: 'pass' | 'floor' | 'fail';
  worstCvd: number;
  offBand: string[];
  lowChroma: string[];
  lowContrast: string[];
}

/** Validate a categorical palette against a surface. `ok` is true unless a check hard-FAILs. */
export function validateCategorical(
  palette: string[],
  { mode, surface }: { mode: 'light' | 'dark'; surface: string },
): CategoricalReport {
  const [lo, hi] = BAND[mode];
  const offBand = palette.filter((c) => {
    const L = oklch(c)[0];
    return L < lo || L > hi;
  });
  const lowChroma = palette.filter((c) => oklch(c)[1] < CHROMA_FLOOR);
  const lowContrast = palette.filter((c) => contrast(c, surface) < CONTRAST_MIN);

  // Worst-case ΔE across BOTH CVD kinds and every adjacent pair in palette order (adjacent
  // categories are the ones most likely to sit next to each other in a legend/chart).
  let worst = Infinity;
  for (const kind of ['protan', 'deutan'] as const) {
    for (let i = 0; i < palette.length - 1; i++) {
      worst = Math.min(worst, deltaE(palette[i], palette[i + 1], kind));
    }
  }
  const cvdState: CategoricalReport['cvdState'] =
    worst >= CVD_TARGET ? 'pass' : worst >= CVD_FLOOR ? 'floor' : 'fail';

  const ok = !offBand.length && !lowChroma.length && cvdState !== 'fail';
  return { ok, cvdState, worstCvd: worst, offBand, lowChroma, lowContrast };
}
