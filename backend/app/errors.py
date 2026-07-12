"""Domain-level errors (S4): framework-free exceptions services raise instead of HTTPException.

A service that raises ``fastapi.HTTPException`` can only be called from the HTTP layer — a
background job or CLI caller gets a web-framework exception that means nothing in its context.
Services raise these instead; ONE FastAPI exception handler (registered in ``app.main``) maps
them to the right status codes, so endpoints need no per-call try/except.

Adopted incrementally: existing services migrate as they are touched.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for domain errors; ``status_code`` drives the HTTP mapping."""

    status_code = 400


class NotFoundError(DomainError):
    """The referenced entity does not exist (or the caller may not see it) → 404."""

    status_code = 404


class ConflictError(DomainError):
    """The request contradicts current state (duplicate, stale, already-resolved) → 409."""

    status_code = 409


class PermissionDeniedError(DomainError):
    """The caller is authenticated but not allowed to do this → 403."""

    status_code = 403
