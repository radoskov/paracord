"""Parser for GROBID TEI XML.

Keep raw TEI in storage. This module extracts normalized records without treating TEI-to-JSON
conversion as lossless.
"""

import re
from dataclasses import dataclass, field

from lxml import etree

TEI_NS = {"t": "http://www.tei-c.org/ns/1.0"}


@dataclass
class ParsedReference:
    raw_citation: str | None = None
    title: str | None = None
    doi: str | None = None
    year: int | None = None


@dataclass
class ParsedPaper:
    title: str | None = None
    abstract: str | None = None
    doi: str | None = None
    authors: list[str] = field(default_factory=list)
    references: list[ParsedReference] = field(default_factory=list)


def _text(element) -> str | None:
    """Return collapsed text content of an element, or None."""
    if element is None:
        return None
    text = " ".join(part.strip() for part in element.itertext() if part.strip())
    return text or None


def _first(*elements):
    """Return the first element that is not None (avoids lxml truthiness pitfalls)."""
    for element in elements:
        if element is not None:
            return element
    return None


def _year(date_element) -> int | None:
    if date_element is None:
        return None
    candidate = date_element.get("when") or _text(date_element) or ""
    match = re.search(r"(\d{4})", candidate)
    return int(match.group(1)) if match else None


def parse_tei(tei_xml: str) -> ParsedPaper:
    """Parse GROBID TEI into PaperRacks structures."""
    paper = ParsedPaper()
    if not tei_xml or not tei_xml.strip():
        return paper
    try:
        root = etree.fromstring(tei_xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return paper

    paper.title = _text(root.find(".//t:teiHeader//t:titleStmt/t:title", TEI_NS))
    paper.abstract = _text(root.find(".//t:profileDesc/t:abstract", TEI_NS))
    paper.doi = _text(root.find('.//t:teiHeader//t:idno[@type="DOI"]', TEI_NS))

    for pers in root.findall(".//t:teiHeader//t:sourceDesc//t:author/t:persName", TEI_NS):
        forenames = " ".join(_text(f) or "" for f in pers.findall("t:forename", TEI_NS)).strip()
        surname = _text(pers.find("t:surname", TEI_NS)) or ""
        name = " ".join(part for part in (forenames, surname) if part).strip()
        if name:
            paper.authors.append(name)

    for bibl in root.findall(".//t:listBibl/t:biblStruct", TEI_NS):
        reference = ParsedReference(
            raw_citation=_text(bibl.find('.//t:note[@type="raw_reference"]', TEI_NS)),
            title=_text(
                _first(
                    bibl.find(".//t:analytic/t:title", TEI_NS),
                    bibl.find(".//t:monogr/t:title", TEI_NS),
                )
            ),
            doi=_text(bibl.find('.//t:idno[@type="DOI"]', TEI_NS)),
            year=_year(
                _first(
                    bibl.find(".//t:monogr//t:date", TEI_NS),
                    bibl.find(".//t:date", TEI_NS),
                )
            ),
        )
        if reference.raw_citation or reference.title or reference.doi:
            paper.references.append(reference)

    return paper
