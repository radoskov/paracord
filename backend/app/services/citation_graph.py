"""Scoped citation graph construction."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GraphScope:
    scope_type: str
    scope_id: str | None = None


def build_scoped_graph(scope: GraphScope) -> dict:
    """Build graph data for library, rack, shelf, search result, or selected works."""
    return {"scope": scope.__dict__, "nodes": [], "edges": []}
