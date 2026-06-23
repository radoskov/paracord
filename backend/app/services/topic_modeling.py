"""Keyword suggestion and BERTopic integration service."""


def queue_topic_model(scope_type: str, scope_id: str | None = None) -> str:
    """Queue a topic-modeling job for a library, rack, shelf, or search result."""
    _ = (scope_type, scope_id)
    return "todo"
