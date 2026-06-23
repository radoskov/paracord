"""External metadata enrichment connectors and merge rules."""


def should_replace_canonical_field(source: str, field_name: str, user_locked: bool) -> bool:
    """Return whether an external assertion may replace a canonical field."""
    if user_locked:
        return False
    return source in {"user", "grobid", "crossref", "arxiv"} and field_name not in {"user_note"}
