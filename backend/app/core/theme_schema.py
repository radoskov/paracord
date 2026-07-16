"""Custom-theme YAML validation + resolution (Theming P4).

Ports the minimal schema the frontend build step (``scripts/build-themes.mjs``) and the compiled
``Theme`` type expect, so an admin-uploaded YAML theme resolves to exactly the same object shape the
bundled themes have — the frontend then renders it through the identical ``renderThemeCss`` /
``VizTheme`` path. Two failure modes:

* **Reject (400)** — malformed YAML, not a mapping, a missing/invalid ``mode``, a bad ``id``, or a
  missing required *token role* (the schema's non-negotiable surface/ink/border/accent/status/
  radius/font set). These raise :class:`ThemeValidationError`.
* **Warn (accepted)** — a ``graph.categorical`` palette that fails the readability check. The
  warnings ride back in the upload response; the theme is still stored (a user may accept it).

Presentational ``graph`` keys that a hand-editor may omit are filled with sensible defaults derived
from the token set, so a partial ``graph`` block is valid (mirrors the design doc's "missing keys
fall back" rule) while the token roles remain required.
"""

import re
from typing import Any

import yaml

from app.core.palette_check import categorical_warnings
from app.core.themes import KNOWN_THEME_IDS

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

# Required role tokens (group -> keys). Mirrors the frontend ThemeTokens interface / EXPECTED_TOKENS
# so a custom theme is structurally identical to a bundled one.
REQUIRED_TOKEN_ROLES: dict[str, tuple[str, ...]] = {
    "surface": ("base", "raised", "overlay", "sunken", "hover"),
    "ink": ("strong", "normal", "muted", "inverse"),
    "border": ("normal", "strong", "focus"),
    "accent": ("primary", "primary-strong", "secondary", "link", "note", "note-bg", "note-border"),
    "status": (
        "success",
        "success-bg",
        "success-border",
        "warning",
        "warning-bg",
        "warning-border",
        "danger",
        "danger-bg",
        "danger-border",
        "info",
        "info-bg",
        "info-border",
    ),
    "radius": ("sm", "md"),
    "font": ("family",),
}


class ThemeValidationError(ValueError):
    """A custom theme's YAML is malformed or missing a required role (maps to HTTP 400)."""


class ResolvedTheme:
    """A validated + palette-resolved theme plus any advisory readability warnings."""

    __slots__ = ("slug", "name", "mode", "temperature", "tokens", "graph", "warnings")

    def __init__(
        self,
        *,
        slug: str,
        name: str,
        mode: str,
        temperature: str,
        tokens: dict[str, dict[str, str]],
        graph: dict[str, Any],
        warnings: list[str],
    ) -> None:
        self.slug = slug
        self.name = name
        self.mode = mode
        self.temperature = temperature
        self.tokens = tokens
        self.graph = graph
        self.warnings = warnings

    def as_theme_object(self) -> dict[str, Any]:
        """The frontend ``Theme`` shape (id/name/mode/temperature/tokens/graph)."""
        return {
            "id": self.slug,
            "name": self.name,
            "mode": self.mode,
            "temperature": self.temperature,
            "tokens": self.tokens,
            "graph": self.graph,
        }

    def swatch(self) -> dict[str, Any]:
        """Representative colours for a picker swatch (surface + primary + a few graph accents)."""
        categorical = self.graph.get("categorical", [])
        return {
            "surface": self.tokens["surface"]["base"],
            "primary": self.tokens["accent"]["primary"],
            "accents": list(categorical[:4]),
        }


def _resolve_refs(node: Any, palette: dict[str, Any]) -> Any:
    """Resolve any ``palette.<key>`` string reference against the theme's palette ramp."""
    if isinstance(node, str):
        if node.startswith("palette."):
            key = node[len("palette.") :]
            if key not in palette:
                raise ThemeValidationError(f"Unknown palette reference: {node!r}")
            return palette[key]
        return node
    if isinstance(node, list):
        return [_resolve_refs(item, palette) for item in node]
    if isinstance(node, dict):
        return {k: _resolve_refs(v, palette) for k, v in node.items()}
    return node


def _require_str(mapping: dict[str, Any], key: str, label: str) -> str:
    """Return the stripped string at ``mapping[key]``, raising ``ThemeValidationError`` (labelled
    ``label``) if it is missing, not a string, or blank."""
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ThemeValidationError(f"Missing or empty required field: {label}")
    return value.strip()


def _validate_tokens(tokens: Any) -> dict[str, dict[str, str]]:
    """Check ``tokens`` against ``REQUIRED_TOKEN_ROLES`` and return the resolved role map.

    Raises ``ThemeValidationError`` if a required role group/key is missing or blank. Extra,
    forward-compatible keys within a known group are preserved (stringified) alongside the
    required ones.
    """
    if not isinstance(tokens, dict):
        raise ThemeValidationError("`tokens` must be a mapping of role groups")
    resolved: dict[str, dict[str, str]] = {}
    for group, keys in REQUIRED_TOKEN_ROLES.items():
        group_val = tokens.get(group)
        if not isinstance(group_val, dict):
            raise ThemeValidationError(f"Missing required token role group: tokens.{group}")
        role: dict[str, str] = {}
        for key in keys:
            value = group_val.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ThemeValidationError(f"Missing required token role: tokens.{group}.{key}")
            role[key] = str(value).strip() if isinstance(value, str) else str(value)
        # Preserve any extra (forward-compatible) keys the author added to the group.
        for key, value in group_val.items():
            if key not in role and value is not None:
                role[key] = str(value)
        resolved[group] = role
    return resolved


def _fill_graph(graph: Any, tokens: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Validate the graph block; require `categorical`, default omitted presentational keys."""
    if not isinstance(graph, dict):
        raise ThemeValidationError("`graph` must be a mapping (needs a `categorical` palette)")
    categorical = graph.get("categorical")
    if not isinstance(categorical, list) or not categorical:
        raise ThemeValidationError("`graph.categorical` must be a non-empty list of colours")
    categorical = [str(c) for c in categorical]

    surface = str(graph.get("surface") or tokens["surface"]["base"])
    sequential = graph.get("sequential")
    if not isinstance(sequential, list) or not sequential:
        sequential = categorical[: min(5, len(categorical))]
    else:
        sequential = [str(c) for c in sequential]
    diverging = graph.get("diverging")
    if not isinstance(diverging, dict):
        diverging = {
            "low": categorical[0],
            "mid": tokens["border"]["normal"],
            "high": categorical[1] if len(categorical) > 1 else categorical[0],
        }
    else:
        diverging = {k: str(v) for k, v in diverging.items()}

    return {
        "surface": surface,
        "text": str(graph.get("text") or tokens["ink"]["normal"]),
        "axis_line": str(graph.get("axis_line") or tokens["border"]["strong"]),
        "split_line": str(graph.get("split_line") or tokens["border"]["normal"]),
        "grid": str(graph.get("grid") or tokens["border"]["normal"]),
        "node_default": str(graph.get("node_default") or tokens["ink"]["muted"]),
        "edge": str(graph.get("edge") or tokens["border"]["strong"]),
        "tooltip_bg": str(graph.get("tooltip_bg") or tokens["ink"]["strong"]),
        "tooltip_text": str(graph.get("tooltip_text") or tokens["surface"]["base"]),
        "warning_ring": str(graph.get("warning_ring") or tokens["status"]["danger"]),
        "font": str(graph.get("font") or tokens["font"]["family"]),
        "categorical": categorical,
        "sequential": sequential,
        "diverging": diverging,
    }


def validate_and_resolve(yaml_text: str) -> ResolvedTheme:
    """Parse + validate + palette-resolve a custom theme YAML.

    Raises :class:`ThemeValidationError` (→ 400) on malformed YAML, a bad ``id``/``mode`` or a
    missing required token role. Collects best-effort readability warnings for the categorical
    palette (never rejects on those).
    """
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ThemeValidationError(f"Malformed YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ThemeValidationError("Theme YAML must be a mapping at the top level")

    slug = _require_str(raw, "id", "id")
    if not _SLUG_RE.match(slug):
        raise ThemeValidationError(
            "`id` must be a slug: lowercase letters, digits and hyphens (max 63 chars)"
        )
    if slug in KNOWN_THEME_IDS:
        raise ThemeValidationError(f"`id` {slug!r} collides with a bundled theme id")

    name = _require_str(raw, "name", "name")
    mode = _require_str(raw, "mode", "mode")
    if mode not in ("light", "dark"):
        raise ThemeValidationError("`mode` must be 'light' or 'dark'")
    temperature = raw.get("temperature")
    temperature = (
        str(temperature).strip()
        if isinstance(temperature, str) and temperature.strip()
        else "custom"
    )

    palette = raw.get("palette") or {}
    if not isinstance(palette, dict):
        raise ThemeValidationError("`palette` must be a mapping when present")

    tokens = _validate_tokens(_resolve_refs(raw.get("tokens"), palette))
    graph = _fill_graph(_resolve_refs(raw.get("graph"), palette), tokens)

    warnings: list[str] = []
    try:
        warnings = categorical_warnings(graph["categorical"], mode=mode, surface=graph["surface"])
    except Exception:  # noqa: BLE001 - readability check is advisory; never block on it
        warnings = []

    return ResolvedTheme(
        slug=slug,
        name=name,
        mode=mode,
        temperature=temperature,
        tokens=tokens,
        graph=graph,
        warnings=warnings,
    )
