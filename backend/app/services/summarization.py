"""Local, external, and human summary service."""


def queue_local_summary(entity_type: str, entity_id: str, model_name: str) -> str:
    """Queue a local LLM summary job and return job ID."""
    _ = (entity_type, entity_id, model_name)
    return "todo"
