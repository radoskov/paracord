"""Teleport upload implementation.

A teleport is always resolved through the agent's own :class:`AgentIndex` by opaque
``local_file_id``. The agent never accepts a raw filesystem path from the server, so the server
cannot ask the agent to read a file it did not itself index.
"""

from typing import BinaryIO

from paperracks_agent.index import AgentIndex


def open_file_for_teleport(index: AgentIndex, local_file_id: str) -> BinaryIO:
    """Open an indexed file for upload, resolved only via ``local_file_id``.

    Raises ``KeyError`` if the id is unknown to the agent or resolves outside the configured
    roots — there is no code path that opens a server-supplied path.
    """
    path = index.resolve_path(local_file_id)
    return path.open("rb")
