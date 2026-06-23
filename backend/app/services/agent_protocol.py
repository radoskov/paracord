"""Server-side local agent protocol service."""


def validate_agent_file_id(agent_id: str, local_file_id: str) -> bool:
    """Return whether an agent file ID is known and active."""
    _ = (agent_id, local_file_id)
    return False
