"""Categorical data-palette readability check (Python port).

A best-effort port of the frontend ``lib/theme/paletteCheck.ts`` (itself a compact port of the
dataviz skill's ``validate_palette.js``) covering the checks computable from colour alone: OKLCH
lightness band, OKLCH chroma floor, WCAG contrast vs the chart surface, and a Machado-2009 CVD ΔE
between adjacent categorical entries. Used by P4 to WARN (never hard-reject) on a custom theme whose
graph palette would read poorly. The bundled build-time check (``make frontend-check``) remains the
authoritative gate for shipped themes; this port only surfaces advisory warnings to the uploader.
"""

import math

_BAND: dict[str, tuple[float, float]] = {
    "light": (0.43, 0.77),
    "dark": (0.48, 0.67),
}
_CHROMA_FLOOR = 0.1
_CVD_TARGET = 12.0
_CVD_FLOOR = 8.0
_CONTRAST_MIN = 3.0

_MACHADO = {
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.01182, 0.04294, 0.968881),
    ),
}


def _hex_to_srgb(value: str) -> tuple[float, float, float]:
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    return tuple(int(s[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _s2lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lin(value: str) -> tuple[float, float, float]:
    r, g, b = _hex_to_srgb(value)
    return _s2lin(r), _s2lin(g), _s2lin(b)


def _rel_lum(value: str) -> float:
    r, g, b = _lin(value)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    hi, lo = sorted((_rel_lum(a), _rel_lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _oklch(value: str) -> tuple[float, float]:
    r, g, b = _lin(value)
    lp = math.pow(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b, 1 / 3)
    mp = math.pow(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b, 1 / 3)
    sp = math.pow(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b, 1 / 3)
    lightness = 0.2104542553 * lp + 0.793617785 * mp - 0.0040720468 * sp
    a = 1.9779984951 * lp - 2.428592205 * mp + 0.4505937099 * sp
    b2 = 0.0259040371 * lp + 0.7827717662 * mp - 0.808675766 * sp
    return lightness, math.hypot(a, b2)


def _lin2lab(r: float, g: float, b: float) -> tuple[float, float, float]:
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b
    z = 0.0193339 * r + 0.119192 * g + 0.9503041 * b

    def f(t: float) -> float:
        return math.pow(t, 1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x / 0.95047), f(y / 1.0), f(z / 1.08883)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _simulate(value: str, kind: str) -> tuple[float, float, float]:
    r, g, b = _lin(value)
    m = _MACHADO[kind]

    def clamp(c: float) -> float:
        return max(0.0, min(1.0, c))

    return (
        clamp(m[0][0] * r + m[0][1] * g + m[0][2] * b),
        clamp(m[1][0] * r + m[1][1] * g + m[1][2] * b),
        clamp(m[2][0] * r + m[2][1] * g + m[2][2] * b),
    )


def _delta_e(a: str, b: str, kind: str) -> float:
    la = _lin2lab(*_simulate(a, kind))
    lb = _lin2lab(*_simulate(b, kind))
    return math.dist(la, lb)


def categorical_warnings(palette: list[str], *, mode: str, surface: str) -> list[str]:
    """Return human-readable readability warnings for a categorical palette (empty = clean).

    Best-effort: any colour that cannot be parsed is skipped so a warning check never raises. This
    is advisory only — the caller WARNs, it does not reject.
    """
    warnings: list[str] = []
    lo, hi = _BAND.get(mode, _BAND["light"])

    parseable: list[str] = []
    for colour in palette:
        try:
            _lin(colour)
            parseable.append(colour)
        except (ValueError, IndexError):
            continue

    off_band = [c for c in parseable if not (lo <= _oklch(c)[0] <= hi)]
    if off_band:
        warnings.append(
            f"{len(off_band)} palette colour(s) sit outside the {mode} categorical lightness band "
            f"[{lo}, {hi}] and may read too light/dark as marks: {', '.join(off_band)}."
        )

    low_chroma = [c for c in parseable if _oklch(c)[1] < _CHROMA_FLOOR]
    if low_chroma:
        warnings.append(
            f"{len(low_chroma)} palette colour(s) fall below the chroma floor ({_CHROMA_FLOOR}) "
            f"and may read as grey: {', '.join(low_chroma)}."
        )

    low_contrast = [c for c in parseable if contrast(c, surface) < _CONTRAST_MIN]
    if low_contrast:
        warnings.append(
            f"{len(low_contrast)} palette colour(s) have < {_CONTRAST_MIN}:1 contrast against the "
            f"chart surface {surface}: {', '.join(low_contrast)}."
        )

    worst = math.inf
    for kind in ("protan", "deutan"):
        for i in range(len(parseable) - 1):
            worst = min(worst, _delta_e(parseable[i], parseable[i + 1], kind))
    if math.isfinite(worst) and worst < _CVD_TARGET:
        band = "below the 8-12 floor (adjacent series may be indistinguishable under colour-vision "
        state = (
            band + "deficiency)"
            if worst < _CVD_FLOOR
            else "in the 8-12 floor band (adjacent series separate only with the legend/labels)"
        )
        warnings.append(
            f"Worst adjacent CVD ΔE is {worst:.1f}, {state}; consider reordering or deepening hues."
        )

    return warnings
