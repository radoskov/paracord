"""XXE / XML-bomb probes (Batch S): the TEI parser (and the shared ``etree.fromstring`` path) must
not resolve external SYSTEM entities (local file or network) and must not expand a billion-laughs
entity, at the pinned lxml. Every parse entry point must fail safe (no leak, no hang, no crash).
"""

from __future__ import annotations

import pytest
from app.services.tei_parser import (
    extract_body_text,
    extract_sections,
    parse_citation_list,
    parse_tei,
)

pytestmark = pytest.mark.safety

_SECRET = "TOPSECRET-XXE-CANARY-9f1c"


def _tei_with_entity(entity_decl: str, body: str = "&xxe;") -> str:
    return (
        '<?xml version="1.0"?>\n'
        f"<!DOCTYPE TEI [ {entity_decl} ]>\n"
        '<TEI xmlns="http://www.tei-c.org/ns/1.0">'
        "<teiHeader><fileDesc><titleStmt>"
        f"<title>{body}</title>"
        "</titleStmt></fileDesc></teiHeader>"
        f"<text><body><div><p>{body}</p></div></body></text>"
        "</TEI>"
    )


def test_parse_tei_does_not_resolve_local_file_entity(tmp_path) -> None:
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(_SECRET)
    xml = _tei_with_entity(f'<!ENTITY xxe SYSTEM "file://{secret_file}">')
    paper = parse_tei(xml)
    assert _SECRET not in (paper.title or "")


def test_extract_body_text_does_not_resolve_local_file_entity(tmp_path) -> None:
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(_SECRET)
    xml = _tei_with_entity(f'<!ENTITY xxe SYSTEM "file://{secret_file}">')
    body = extract_body_text(xml)
    assert _SECRET not in (body or "")


def test_extract_sections_does_not_resolve_local_file_entity(tmp_path) -> None:
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(_SECRET)
    xml = _tei_with_entity(f'<!ENTITY xxe SYSTEM "file://{secret_file}">')
    sections = extract_sections(xml)
    assert all(_SECRET not in text for _label, text in sections)


def test_parse_tei_does_not_fetch_network_system_entity() -> None:
    # A network SYSTEM entity must not be fetched (no_network default); parse returns safely.
    xml = _tei_with_entity('<!ENTITY xxe SYSTEM "http://169.254.169.254/latest/meta-data">')
    paper = parse_tei(xml)
    assert "169.254" not in (paper.title or "")


def _billion_laughs(levels: int) -> str:
    """Build a nested-entity XML bomb with ``levels`` of 10x self-reference."""
    ents = ['<!ENTITY lol0 "lol">']
    for i in range(1, levels + 1):
        ents.append(f'<!ENTITY lol{i} "{"".join(f"&lol{i - 1};" for _ in range(10))}">')
    dtd = "<!DOCTYPE t [ " + "".join(ents) + " ]>"
    return (
        '<?xml version="1.0"?>\n' + dtd + '<TEI xmlns="http://www.tei-c.org/ns/1.0">'
        f"<teiHeader><fileDesc><titleStmt><title>&lol{levels};</title>"
        "</titleStmt></fileDesc></teiHeader></TEI>"
    )


def test_parse_tei_refuses_deep_billion_laughs() -> None:
    # A deep nested-entity bomb (10^8) would be catastrophic if expanded. At the pinned lxml the
    # entity-expansion limit refuses it: parse returns safely with the entity UNEXPANDED (no hang,
    # no MemoryError, no crash).
    paper = parse_tei(_billion_laughs(8))
    assert len(paper.title or "") == 0


def test_parse_tei_bounds_moderate_entity_expansion() -> None:
    # A shallow expansion is bounded (linear, instant) — never exponential.
    paper = parse_tei(_billion_laughs(4))
    assert len(paper.title or "") <= 100_000


def test_parse_citation_list_survives_entity_payload(tmp_path) -> None:
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(_SECRET)
    xml = (
        '<?xml version="1.0"?>\n'
        f'<!DOCTYPE TEI [ <!ENTITY xxe SYSTEM "file://{secret_file}"> ]>\n'
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><listBibl>'
        "<biblStruct><analytic><title>&xxe;</title></analytic></biblStruct>"
        "</listBibl></TEI>"
    )
    refs = parse_citation_list(xml)
    assert all(_SECRET not in (r.title or "") for r in refs)
