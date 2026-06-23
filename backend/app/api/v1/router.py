"""Versioned API router."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    agents,
    ai,
    auth,
    citations,
    exports,
    files,
    graph,
    health,
    imports,
    racks,
    shelves,
    works,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_router.include_router(imports.router, prefix="/imports", tags=["imports"])
api_router.include_router(files.router, prefix="/files", tags=["files"])
api_router.include_router(works.router, prefix="/works", tags=["works"])
api_router.include_router(shelves.router, prefix="/shelves", tags=["shelves"])
api_router.include_router(racks.router, prefix="/racks", tags=["racks"])
api_router.include_router(citations.router, prefix="/citations", tags=["citations"])
api_router.include_router(graph.router, prefix="/graph", tags=["graph"])
api_router.include_router(exports.router, prefix="/exports", tags=["exports"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
