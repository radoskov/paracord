"""Real CSL (Citation Style Language) rendering for styled bibliography exports.

Delegates to ``citeproc-py`` using the official CSL style files bundled under ``styles/`` and
citeproc-py's own en-US locale. See ``NOTICE`` in this directory for license attribution — the
``.csl`` files are from the CSL project and licensed CC-BY-SA 3.0.
"""

from app.services.csl.engine import (
    CITATION_STYLES,
    STYLE_LABELS,
    available_styles,
    render_bibliography,
)

__all__ = [
    "CITATION_STYLES",
    "STYLE_LABELS",
    "available_styles",
    "render_bibliography",
]
