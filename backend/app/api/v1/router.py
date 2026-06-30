"""Versioned API router."""

from fastapi import APIRouter, Depends

from app.api.deps import require_authenticated_user
from app.api.v1.endpoints import (
    admin,
    agents,
    ai,
    ai_admin,
    auth,
    citations,
    duplicates,
    exports,
    files,
    graph,
    health,
    import_roots,
    imports,
    jobs,
    preferences,
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
# Admin routes enforce {owner, admin} per-endpoint via require_admin; the privileged
# admin-management subset is owner-only, enforced in the user-management service layer.
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
# AI provider config + model management (owner or admin; WORKPLAN_NEXT Stage 8).
api_router.include_router(ai_admin.router, prefix="/admin", tags=["admin", "ai"])
# Server import roots GUI (batch 2 #19). Owner-only, enforced per-endpoint via require_owner.
api_router.include_router(import_roots.router, prefix="/admin", tags=["admin", "import-roots"])
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
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"], dependencies=auth_required)
api_router.include_router(
    search.router, prefix="/search", tags=["search"], dependencies=auth_required
)
# Per-user UI preferences (any authenticated user manages their own blob).
api_router.include_router(
    preferences.router, prefix="/preferences", tags=["preferences"], dependencies=auth_required
)
