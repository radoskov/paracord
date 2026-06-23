"""Citation and bibliography export service."""

SUPPORTED_FORMATS = {"bibtex", "biblatex", "ris", "csl-json", "markdown", "html", "text"}


def export_bibliography(scope_type: str, output_format: str, scope_id: str | None = None) -> str:
    """Export bibliography content for a scope."""
    if output_format not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported export format: {output_format}")
    _ = (scope_type, scope_id)
    return ""
