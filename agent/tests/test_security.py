"""Agent filesystem security tests."""

from pathlib import Path

from paperracks_agent.security import is_path_within_roots


def test_path_inside_root() -> None:
    assert is_path_within_roots(Path("/tmp/example/a.pdf"), [Path("/tmp/example")])


def test_path_outside_root() -> None:
    assert not is_path_within_roots(Path("/tmp/other/a.pdf"), [Path("/tmp/example")])


# --- D3: transport guard ----------------------------------------------------------------------


def test_plaintext_http_to_non_loopback_is_refused() -> None:
    import pytest
    from paperracks_agent.client import InsecureTransportError, check_transport

    with pytest.raises(InsecureTransportError):
        check_transport("http://192.168.1.10:8000")


def test_plaintext_loopback_https_and_optin_pass() -> None:
    from paperracks_agent.client import check_transport

    check_transport("http://127.0.0.1:8000")
    check_transport("http://localhost:8000")
    check_transport("https://paracord.lan")
    check_transport("http://192.168.1.10:8000", allow_insecure_http=True)


def test_client_uses_ca_cert_as_verify() -> None:
    from paperracks_agent.client import PaRacORDServerClient

    client = PaRacORDServerClient("https://paracord.lan", ca_cert="/tmp/root.crt")
    assert client.verify == "/tmp/root.crt"
    assert PaRacORDServerClient("https://paracord.lan").verify is True
