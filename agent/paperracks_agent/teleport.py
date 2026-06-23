"""Teleport upload implementation."""

from pathlib import Path


def open_file_for_teleport(path: Path):
    """Open a file selected by local file ID for server upload.

    TODO: Resolve local_file_id through the agent index. Do not accept arbitrary raw path requests
    from the server.
    """
    return path.open("rb")
