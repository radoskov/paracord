"""Known GUI theme identifiers.

The themes themselves are authored as YAML under ``frontend/themes/`` and compiled to the
frontend theme registry. The backend only needs the set of valid ids so it can validate a
per-user ``theme`` preference (``User.theme``) — ``NULL`` means "use the boot default". Keep
this set in sync when a bundled theme is added or removed.
"""

DEFAULT_THEME_ID = "latte-warm"

KNOWN_THEME_IDS: frozenset[str] = frozenset(
    {
        "latte-warm",
        "latte-cool",
        "mocha-warm",
        "mocha-cool",
    }
)


def is_known_theme(theme_id: str) -> bool:
    """Return whether ``theme_id`` names a bundled theme."""
    return theme_id in KNOWN_THEME_IDS
