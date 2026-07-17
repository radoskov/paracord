"""Global error envelope + request-id middleware (main.create_app `_error_envelope`).

Motivation (2026-07-17): a PDF whose text layer carried NUL bytes made the files INSERT raise
`sqlalchemy.DataError`, which surfaced as a bare 500 without CORS headers — the browser blocked
it and fetch() reported only "NetworkError when attempting to fetch resource", hiding the real
cause. The envelope middleware (wrapped by CORSMiddleware, unlike a Starlette `Exception`
handler) turns every otherwise-unhandled exception into descriptive JSON tagged with a request
id that also lands in the server-log traceback.
"""

from __future__ import annotations

from sqlalchemy.exc import DataError


def _add_failing_routes(app) -> None:
    """Register throwaway routes that raise; added lazily so only these tests see them."""
    existing = {getattr(r, "path", None) for r in app.routes}
    if "/boom-generic" in existing:
        return

    @app.get("/boom-generic")
    def boom_generic():
        raise RuntimeError("the flux capacitor is misaligned")

    @app.get("/boom-dataerror")
    def boom_dataerror():
        raise DataError(
            "INSERT INTO files ...",
            {},
            Exception("PostgreSQL text fields cannot contain NUL (0x00) bytes"),
        )


def test_unhandled_exception_becomes_descriptive_500(client):
    _add_failing_routes(client.app)
    resp = client.get("/boom-generic")
    assert resp.status_code == 500
    body = resp.json()
    # The class, the message, and the request id are all in the human-facing detail.
    assert "RuntimeError" in body["detail"]
    assert "flux capacitor" in body["detail"]
    assert body["request_id"] in body["detail"]
    assert resp.headers["x-request-id"] == body["request_id"]


def test_dataerror_maps_to_400_with_db_reason(client):
    _add_failing_routes(client.app)
    resp = client.get("/boom-dataerror")
    assert resp.status_code == 400
    body = resp.json()
    assert "cannot contain NUL" in body["detail"]
    assert body["request_id"] in body["detail"]


def test_every_response_carries_a_request_id(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert len(resp.headers["x-request-id"]) >= 8


def test_incoming_request_id_is_honored(client):
    resp = client.get("/api/v1/health", headers={"X-Request-ID": "trace-me-42"})
    assert resp.headers["x-request-id"] == "trace-me-42"


def test_http_exceptions_keep_their_own_detail(client):
    """Normal HTTPExceptions (404s etc.) are untouched by the envelope."""
    resp = client.get("/api/v1/works/00000000-0000-0000-0000-000000000000")
    assert resp.status_code in (401, 403, 404)  # unauthenticated → auth error, still JSON detail
    assert "detail" in resp.json()
    assert resp.headers["x-request-id"]
