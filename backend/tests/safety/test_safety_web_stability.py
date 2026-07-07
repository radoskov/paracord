"""Security-header / CSP presence + web-stability probes (Batch S).

The CSP/header assertions read the committed nginx config when it is reachable from the test runner
(it lives in the frontend image, not the API container, so this cleanly SKIPS when absent). The
stability probes are best-effort and deterministic: bounded concurrency against a cheap endpoint and
an oversized query string must not wedge the app — it stays responsive and never hangs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety


def _find_nginx_conf() -> Path | None:
    for candidate in (
        Path("/app/frontend/nginx.conf"),
        Path(__file__).resolve().parents[3] / "frontend" / "nginx.conf",
    ):
        if candidate.exists():
            return candidate
    return None


_REQUIRED_DIRECTIVES = [
    "Content-Security-Policy",
    "default-src 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "nosniff",
    "Referrer-Policy",
]


def test_nginx_config_declares_security_headers() -> None:
    conf = _find_nginx_conf()
    if conf is None:
        pytest.skip("frontend/nginx.conf not reachable from this test runner (frontend image only)")
    text = conf.read_text()
    for directive in _REQUIRED_DIRECTIVES:
        assert directive in text, f"missing security directive: {directive!r}"
    # The headers must also be repeated in the asset location block (add_header there would
    # otherwise suppress the inherited server-level ones), so they appear more than once.
    assert text.count("Content-Security-Policy") >= 2


# --- web stability (best-effort, deterministic) ------------------------------------------------


def test_health_stays_responsive_under_concurrency(client) -> None:
    def hit(_i: int) -> int:
        return client.get("/api/v1/health").status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(hit, range(48)))
    assert statuses == [200] * 48  # every request served, none wedged


def test_sustained_authenticated_reads_stay_responsive(client, auth_headers) -> None:
    # A sustained burst of authenticated DB-backed reads must all be served (no wedge/backlog).
    # Kept sequential: the test harness shares a single in-memory SQLite connection, so true
    # parallel DB access is a harness artifact, not an app-stability signal — the health probe
    # above exercises genuine concurrency on a DB-free path.
    headers = auth_headers("reader")
    statuses = [client.get("/api/v1/works", headers=headers).status_code for _ in range(30)]
    assert statuses == [200] * 30


def test_oversized_query_string_does_not_wedge(client, auth_headers) -> None:
    huge = "a" * 50_000
    resp = client.get("/api/v1/works", headers=auth_headers("reader"), params={"q": huge})
    assert resp.status_code in (200, 414, 422)  # handled, not a hang/500
    # App remains responsive afterwards.
    assert client.get("/api/v1/health").status_code == 200
