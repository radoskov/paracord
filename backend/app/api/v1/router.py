"""Versioned API router."""

from fastapi import APIRouter, Depends

from app.api.deps import require_authenticated_user
from app.api.v1.endpoints import (
    admin,
    agents,
    ai,
    ai_admin,
    auth,
    backups,
    citations,
    duplicates,
    exports,
    files,
    graph,
    groups,
    health,
    import_roots,
    imports,
    jobs,
    preferences,
    racks,
    saved_filters,
    search,
    shelves,
    sources,
    tags,
    themes,
    visualization,
    web_find_allowed_hosts,
    works,
)

api_router = APIRouter()
auth_required = [Depends(require_authenticated_user)]

api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# Admin routes enforce {owner, admin} per-endpoint via require_admin; the privileged
# admin-management subset is owner-only, enforced in the user-management service layer.
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(backups.router, prefix="/admin/backups", tags=["admin", "backups"])
# AI provider config + model management (owner or admin; WORKPLAN_NEXT Stage 8).
api_router.include_router(ai_admin.router, prefix="/admin", tags=["admin", "ai"])
# Server import roots GUI (batch 2 #19). Owner-only, enforced per-endpoint via require_owner.
api_router.include_router(import_roots.router, prefix="/admin", tags=["admin", "import-roots"])
# Find-on-web allowed download hosts GUI (batch 2 #5). Admin-or-owner, enforced via require_admin.
api_router.include_router(
    web_find_allowed_hosts.router, prefix="/admin", tags=["admin", "web-find"]
)
# Phase H access control: user groups, grants, default grants, access settings. Admin-or-owner,
# enforced per-endpoint via require_admin.
api_router.include_router(groups.router, prefix="/admin", tags=["admin", "groups"])
# Runtime custom themes (Theming P4): write (upload/replace/delete) is owner/admin, enforced
# per-endpoint via require_admin. The read side is mounted separately at /themes below.
api_router.include_router(themes.admin_router, prefix="/admin", tags=["admin", "themes"])
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
# D38 visualization module (Track C): provider-registry-backed view payloads (temporal_map in P2).
api_router.include_router(
    visualization.router, prefix="/viz", tags=["visualization"], dependencies=auth_required
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
# Custom themes read side (Theming P4): any authenticated user may list custom themes for their
# picker and fetch a resolved theme to apply. Uploads/deletes are the owner/admin admin_router.
api_router.include_router(
    themes.router, prefix="/themes", tags=["themes"], dependencies=auth_required
)
# Per-user saved library filters (Phase B7; any authenticated user manages their own).
api_router.include_router(
    saved_filters.router,
    prefix="/saved-filters",
    tags=["saved-filters"],
    dependencies=auth_required,
)
