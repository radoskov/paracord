"""SSRF probes (Batch S): the egress guards must refuse internal/loopback/link-local/metadata
targets, non-http(s) schemes, shadow-library hosts, and cross-host redirects that escape the guard,
and the admin-set ``ollama_url`` must reject non-local hosts unless explicitly opted in.

These drive the guard functions with an INJECTED resolver so no real DNS/network is ever touched.
"""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.services import web_find
from app.services.ai_config import _validate_ollama_url
from app.services.web_find import (
    DownloadRefused,
    _classify_download_host,
    _host_resolves_internal,
    _ip_is_internal,
    _stream_pdf,
    resolve_final_url,
)

pytestmark = pytest.mark.safety

# The cloud-metadata IP (AWS/GCP/Azure link-local) — the canonical SSRF pivot.
_METADATA_IP = "169.254.169.254"

_INTERNAL_IPS = [
    "127.0.0.1",
    "127.5.5.5",
    "10.0.0.1",
    "172.16.0.5",
    "192.168.1.10",
    _METADATA_IP,  # link-local
    "0.0.0.0",  # unspecified
    "::1",  # IPv6 loopback
    "fe80::1",  # IPv6 link-local
    "fc00::1",  # IPv6 unique-local (private)
]

_PUBLIC_IPS = ["93.184.216.34", "8.8.8.8", "1.1.1.1"]


@pytest.mark.parametrize("ip", _INTERNAL_IPS)
def test_ip_is_internal_flags_all_private_ranges(ip: str) -> None:
    assert _ip_is_internal(ip) is True


@pytest.mark.parametrize("ip", _PUBLIC_IPS)
def test_ip_is_internal_allows_public(ip: str) -> None:
    assert _ip_is_internal(ip) is False


def test_ip_is_internal_treats_unparsable_as_unsafe() -> None:
    assert _ip_is_internal("not-an-ip") is True


def test_host_resolves_internal_uses_injected_resolver() -> None:
    internal = lambda host: [_METADATA_IP]  # noqa: E731
    public = lambda host: ["93.184.216.34"]  # noqa: E731
    assert _host_resolves_internal("http://metadata.evil.test/x", resolver=internal) is True
    assert _host_resolves_internal("http://example.com/x", resolver=public) is False


def test_host_resolves_internal_fails_closed_on_resolution_error() -> None:
    def boom(host: str):
        raise OSError("nxdomain")

    assert _host_resolves_internal("http://nope.test/x", resolver=boom) is True


def test_host_resolves_internal_fails_closed_on_empty_result() -> None:
    assert _host_resolves_internal("http://nope.test/x", resolver=lambda h: []) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/api/v1/health",
        "http://192.168.1.1/admin",
    ],
)
def test_classify_download_host_hard_blocks_internal_targets(url: str) -> None:
    outcome, _reason = _classify_download_host(
        url,
        policy="unrestricted",
        merged_allowed=set(web_find.DEFAULT_ALLOWED_HOSTS),
        resolver=lambda host: [url.split("//", 1)[1].split("/", 1)[0].split(":")[0]],
        check_ip=True,
    )
    assert outcome == "hard_block"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/1", "ftp://host/x"])
def test_classify_download_host_hard_blocks_bad_scheme(url: str) -> None:
    outcome, _reason = _classify_download_host(
        url, policy="unrestricted", merged_allowed=set(), check_ip=False
    )
    assert outcome == "hard_block"


@pytest.mark.parametrize(
    "url",
    ["https://sci-hub.se/10.1/x", "https://libgen.rs/book", "http://annas-archive.org/x"],
)
def test_classify_download_host_hard_blocks_shadow_libraries(url: str) -> None:
    outcome, _reason = _classify_download_host(
        url, policy="unrestricted", merged_allowed=set(), check_ip=False
    )
    assert outcome == "hard_block"


def test_resolve_final_url_refuses_internal_before_any_request() -> None:
    # The internal-IP guard runs on the initial URL BEFORE any network call, so an injected
    # resolver returning a metadata IP short-circuits to None with no real request.
    result = resolve_final_url(
        "http://metadata.evil.test/x", timeout=0.01, resolver=lambda host: [_METADATA_IP]
    )
    assert result is None


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://sci-hub.se/x"])
def test_resolve_final_url_refuses_bad_scheme_and_shadow(url: str) -> None:
    assert resolve_final_url(url, timeout=0.01, resolver=lambda host: ["93.184.216.34"]) is None


def test_stream_pdf_raises_on_internal_target_without_policy() -> None:
    with pytest.raises(DownloadRefused):
        _stream_pdf(
            "http://169.254.169.254/latest/meta-data",
            timeout=0.01,
            max_bytes=1024,
            resolver=lambda host: [_METADATA_IP],
        )


def test_stream_pdf_raises_on_shadow_library_without_policy() -> None:
    with pytest.raises(DownloadRefused):
        _stream_pdf(
            "https://sci-hub.se/paper.pdf",
            timeout=0.01,
            max_bytes=1024,
            resolver=lambda host: ["93.184.216.34"],
        )


def test_download_and_attach_blocks_shadow_host_without_network(db) -> None:
    # scheme + denylist are hard blocks checked before any fetch (check_ip deferred), so a
    # shadow-library candidate is refused with no network access and stores nothing.
    result = web_find.download_and_attach(
        db,
        work=None,
        candidate_url="https://sci-hub.se/paper.pdf",
        source="test",
        actor=None,
        settings=Settings(),
    )
    assert result["status"] == "blocked"


# --- admin ollama_url SSRF guard (D6) ---------------------------------------------------------


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "ollama", "::1"])
def test_ollama_url_accepts_local_and_service_hosts(host: str) -> None:
    # No exception == accepted. Loopback + single-label docker-service names are always safe.
    url = f"http://[{host}]:11434" if ":" in host else f"http://{host}:11434"
    _validate_ollama_url(url, settings=Settings(allow_external_ollama=False))


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254:11434",  # cloud metadata
        "http://192.168.1.50:11434",  # LAN
        "http://evil.example.com:11434",  # public FQDN
        "http://10.0.0.9:11434",  # RFC1918
    ],
)
def test_ollama_url_rejects_non_local_hosts_by_default(url: str) -> None:
    with pytest.raises(ValueError):
        _validate_ollama_url(url, settings=Settings(allow_external_ollama=False))


@pytest.mark.parametrize("url", ["ftp://ollama:11434", "gopher://localhost", "file:///x"])
def test_ollama_url_rejects_non_http_scheme(url: str) -> None:
    with pytest.raises(ValueError):
        _validate_ollama_url(url, settings=Settings(allow_external_ollama=False))


def test_ollama_url_external_allowed_only_with_explicit_optin() -> None:
    # The escape hatch: an external host is accepted only when the operator opts in.
    _validate_ollama_url(
        "http://gpu-box.example.com:11434", settings=Settings(allow_external_ollama=True)
    )


def test_admin_ai_config_endpoint_rejects_internal_ollama_url(client, auth_headers) -> None:
    resp = client.put(
        "/api/v1/admin/ai-config",
        headers=auth_headers("owner"),
        json={"ollama_url": "http://169.254.169.254:11434"},
    )
    assert resp.status_code == 400
