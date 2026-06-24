"""Parser for GROBID TEI XML.

Keep raw TEI in storage. This module extracts normalized records without treating TEI-to-JSON
conversion as lossless.
"""

import re
from dataclasses import dataclass, field

from lxml import etree

TEI_NS = {"t": "http://www.tei-c.org/ns/1.0"}
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


@dataclass
class ParsedReference:
    key: str | None = None
    raw_citation: str | None = None
    title: str | None = None
    doi: str | None = None
    year: int | None = None


@dataclass
class ParsedCitationMention:
    reference_key: str
    marker_text: str | None = None
    section_label: str | None = None
    context_before: str | None = None
    context_sentence: str | None = None
    context_after: str | None = None


@dataclass
class ParsedPaper:
    title: str | None = None
    abstract: str | None = None
    doi: str | None = None
    authors: list[str] = field(default_factory=list)
    references: list[ParsedReference] = field(default_factory=list)
    citation_mentions: list[ParsedCitationMention] = field(default_factory=list)


def _text(element) -> str | None:
    """Return collapsed text content of an element, or None."""
    if element is None:
        return None
    text = " ".join(part.strip() for part in element.itertext() if part.strip())
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
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

    for index, bibl in enumerate(root.findall(".//t:listBibl/t:biblStruct", TEI_NS)):
        reference = ParsedReference(
            key=bibl.get(XML_ID) or bibl.get("id") or f"b{index}",
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

    for ref in root.findall('.//t:text//t:body//t:ref[@type="bibr"]', TEI_NS):
        target = (ref.get("target") or "").strip()
        reference_key = target.removeprefix("#")
        if not reference_key:
            continue
        mention = ParsedCitationMention(
            reference_key=reference_key,
            marker_text=_text(ref),
            section_label=_section_label(ref),
            context_before=_neighbor_sentence(ref, previous=True),
            context_sentence=_context_sentence(ref),
            context_after=_neighbor_sentence(ref, previous=False),
        )
        paper.citation_mentions.append(mention)

    return paper


def _context_sentence(element) -> str | None:
    sentence = _ancestor(element, "s")
    if sentence is not None:
        return _text(sentence)
    paragraph = _ancestor(element, "p")
    return _text(paragraph)


def _neighbor_sentence(element, *, previous: bool) -> str | None:
    sentence = _ancestor(element, "s")
    if sentence is None:
        return None
    sibling = sentence.getprevious() if previous else sentence.getnext()
    while sibling is not None and not _is_tei_tag(sibling, "s"):
        sibling = sibling.getprevious() if previous else sibling.getnext()
    return _text(sibling)


def _section_label(element) -> str | None:
    div = _ancestor(element, "div")
    if div is None:
        return None
    return _text(div.find("t:head", TEI_NS)) or div.get("type")


def _ancestor(element, local_name: str):
    current = element.getparent()
    while current is not None:
        if _is_tei_tag(current, local_name):
            return current
        current = current.getparent()
    return None


def _is_tei_tag(element, local_name: str) -> bool:
    return element.tag == f"{{{TEI_NS['t']}}}{local_name}"
