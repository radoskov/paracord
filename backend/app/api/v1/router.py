"""Versioned API router."""

from fastapi import APIRouter, Depends

from app.api.deps import require_authenticated_user
from app.api.v1.endpoints import (
    admin,
    agents,
    ai,
    auth,
    citations,
    duplicates,
    exports,
    files,
    graph,
    health,
    imports,
    racks,
    search,
    shelves,
    sources,
    tags,
    works,
)

api_router = APIRouter()
auth_required = [Depends(require_authenticated_user)]

api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# Admin routes enforce owner role per-endpoint via require_owner.
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
# Agent routes authenticate via the enrollment/agent token, not a user session, so the
# router is not behind the user-session dependency.
api_router.include_router(
    agents.router,
    prefix="/agents",
    tags=["agents"],
)
api_router.include_router(
    sources.router,
    prefix="/sources",
    tags=["sources"],
    dependencies=auth_required,
)
api_router.include_router(
    imports.router,
    prefix="/imports",
    tags=["imports"],
    dependencies=auth_required,
)
api_router.include_router(files.router, prefix="/files", tags=["files"], dependencies=auth_required)
api_router.include_router(works.router, prefix="/works", tags=["works"], dependencies=auth_required)
api_router.include_router(
    shelves.router,
    prefix="/shelves",
    tags=["shelves"],
    dependencies=auth_required,
)
api_router.include_router(racks.router, prefix="/racks", tags=["racks"], dependencies=auth_required)
api_router.include_router(tags.router, prefix="/tags", tags=["tags"], dependencies=auth_required)
api_router.include_router(
    citations.router,
    prefix="/citations",
    tags=["citations"],
    dependencies=auth_required,
)
api_router.include_router(
    duplicates.router,
    prefix="/duplicates",
    tags=["duplicates"],
    dependencies=auth_required,
)
api_router.include_router(
    graph.router, prefix="/graphs", tags=["graph"], dependencies=auth_required
)
api_router.include_router(
    exports.router,
    prefix="/exports",
    tags=["exports"],
    dependencies=auth_required,
)
api_router.include_router(ai.router, prefix="/ai", tags=["ai"], dependencies=auth_required)
api_router.include_router(
    search.router, prefix="/search", tags=["search"], dependencies=auth_required
)
