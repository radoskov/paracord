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
    """One bibliography entry parsed from a TEI ``biblStruct`` (a reference the paper cites)."""

    key: str | None = None
    raw_citation: str | None = None
    title: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    # Additive (Phase J batch import): the citation's authors and venue, when GROBID resolves them.
    authors: list[str] = field(default_factory=list)
    venue: str | None = None


@dataclass
class ParsedCitationMention:
    """One in-text citation marker (e.g. "[3]") linked to its reference and surrounding context."""

    reference_key: str
    marker_text: str | None = None
    section_label: str | None = None
    context_before: str | None = None
    context_sentence: str | None = None
    context_after: str | None = None
    page: int | None = None
    pdf_coordinates: list[dict[str, float | int]] = field(default_factory=list)


@dataclass
class ParsedPaper:
    """The normalized record extracted from one GROBID TEI document (:func:`parse_tei`)."""

    title: str | None = None
    abstract: str | None = None
    doi: str | None = None
    # Issue 11: the primary paper's own venue (journal/conference) and publication year, mined from
    # the TEI header's monograph like references already are. GROBID often can't fill these, but when
    # it does they populate the first-class Work.venue / Work.year fields directly from extraction.
    venue: str | None = None
    year: int | None = None
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


def extract_body_text(tei_xml: str) -> str | None:
    """Return the concatenated body paragraph text from a GROBID TEI document."""
    try:
        root = etree.fromstring(tei_xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return None
    paragraphs = [_text(p) for p in root.findall(".//t:text/t:body//t:p", TEI_NS)]
    joined = " ".join(p for p in paragraphs if p)
    return joined or None


def extract_sections(tei_xml: str) -> list[tuple[str | None, str]]:
    """Return ``(section_label, text)`` for each top-level body section of a GROBID TEI document.

    Each ``<div>`` directly under ``<body>`` becomes one section: its label is the ``<head>`` text
    (or the div ``type`` attribute), and its text is the concatenation of all descendant paragraphs.
    Used by the chunker (HYBRID-SEARCH-DESIGN §3.1) to keep section labels on chunks. Returns ``[]``
    on malformed/empty TEI. The reference list lives under ``<back>``, not ``<body>``, so it is
    naturally excluded here; the chunker additionally drops acknowledgment-like sections by label.
    """
    if not tei_xml or not tei_xml.strip():
        return []
    try:
        root = etree.fromstring(tei_xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return []
    sections: list[tuple[str | None, str]] = []
    for div in root.findall(".//t:text/t:body/t:div", TEI_NS):
        label = _numbered_head_label(div)
        text = " ".join(t for p in div.findall(".//t:p", TEI_NS) if (t := _text(p)))
        if text:
            sections.append((label, text))
    return sections


def _numbered_head_label(div) -> str | None:
    """Section label including its number. GROBID puts the section number either inline in the head
    ("I. Introduction") or in a ``head @n`` attribute ("1.", "2.1.") with a bare-title head. Prefix
    the ``@n`` when present and not already in the head so downstream main/sub detection has a
    uniform "<number> <title>" form (2026-07-16). (Distinct from the citation-mapping
    ``_section_label(element)`` below, which walks to an element's ancestor div.)"""
    head = div.find("t:head", TEI_NS)
    text = _text(head) if head is not None else None
    text = text or div.get("type")
    n = (head.get("n") if head is not None else None) or ""
    n = n.strip()
    if n and (not text or not text.lstrip().startswith(n.rstrip("."))):
        return f"{n} {text}".strip() if text else n
    return text


def extract_leaf_sections(tei_xml: str) -> list[tuple[str | None, str]]:
    """Return ``(label, text)`` at SUBSECTION granularity (2026-07-16, for the 'deep' summary).

    GROBID nests subsections as ``<div>`` inside a top-level body ``<div>``. For each top-level
    section: if it has child ``<div>``s, emit one entry per child (label ``"Parent › Child"``, text =
    that child's own paragraphs) plus the parent's own lead paragraphs when present; otherwise emit
    the section as a whole. Falls back to :func:`extract_sections` granularity when nothing nests.
    """
    if not tei_xml or not tei_xml.strip():
        return []
    try:
        root = etree.fromstring(tei_xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return []
    out: list[tuple[str | None, str]] = []
    for div in root.findall(".//t:text/t:body/t:div", TEI_NS):
        head = _numbered_head_label(div)
        child_divs = div.findall("t:div", TEI_NS)
        if not child_divs:
            text = " ".join(t for p in div.findall(".//t:p", TEI_NS) if (t := _text(p)))
            if text:
                out.append((head, text))
            continue
        # Parent's own lead paragraphs (direct children, before the first subsection).
        lead = " ".join(t for p in div.findall("t:p", TEI_NS) if (t := _text(p)))
        if lead:
            out.append((head, lead))
        for sub in child_divs:
            sub_head = _numbered_head_label(sub)
            label = f"{head} › {sub_head}" if head and sub_head else (sub_head or head)
            text = " ".join(t for p in sub.findall(".//t:p", TEI_NS) if (t := _text(p)))
            if text:
                out.append((label, text))
    return out


def _first(*elements):
    """Return the first element that is not None (avoids lxml truthiness pitfalls)."""
    for element in elements:
        if element is not None:
            return element
    return None


def _year(date_element) -> int | None:
    """Extract a 4-digit year from a TEI ``date`` element's ``@when`` or text content."""
    if date_element is None:
        return None
    candidate = date_element.get("when") or _text(date_element) or ""
    match = re.search(r"(\d{4})", candidate)
    return int(match.group(1)) if match else None


def _persname_to_name(pers) -> str | None:
    """Render a TEI ``persName`` element to a "Forename Surname" string, or None."""
    forenames = " ".join(_text(f) or "" for f in pers.findall("t:forename", TEI_NS)).strip()
    surname = _text(pers.find("t:surname", TEI_NS)) or ""
    name = " ".join(part for part in (forenames, surname) if part).strip()
    return name or None


def _biblstruct_authors(bibl) -> list[str]:
    """Extract author display names from a ``biblStruct`` (analytic authors, else monogr)."""
    persons = bibl.findall(".//t:analytic/t:author/t:persName", TEI_NS)
    if not persons:
        persons = bibl.findall(".//t:author/t:persName", TEI_NS)
    names = [_persname_to_name(p) for p in persons]
    return [n for n in names if n]


def _biblstruct_to_reference(bibl, index: int) -> ParsedReference:
    """Convert one TEI ``biblStruct`` element into a :class:`ParsedReference`.

    Shared by the full-text reference list (``parse_tei``) and the citation-list parser
    (``parse_citation_list``). The venue is the monograph title when an analytic (article) title is
    present (so the analytic title stays the reference title and the monograph is the journal/book).
    """
    analytic_title = bibl.find(".//t:analytic/t:title", TEI_NS)
    monogr_title = bibl.find(".//t:monogr/t:title", TEI_NS)
    title = _text(_first(analytic_title, monogr_title))
    # Only treat the monograph title as the venue when it isn't itself the reference title.
    venue = _text(monogr_title) if analytic_title is not None else None
    return ParsedReference(
        key=bibl.get(XML_ID) or bibl.get("id") or f"b{index}",
        raw_citation=_text(bibl.find('.//t:note[@type="raw_reference"]', TEI_NS)),
        title=title,
        doi=_text(bibl.find('.//t:idno[@type="DOI"]', TEI_NS)),
        arxiv_id=_text(bibl.find('.//t:idno[@type="arXiv"]', TEI_NS)),
        year=_year(
            _first(
                bibl.find(".//t:monogr//t:date", TEI_NS),
                bibl.find(".//t:date", TEI_NS),
            )
        ),
        authors=_biblstruct_authors(bibl),
        venue=venue,
    )


def parse_citation_list(tei_xml: str) -> list[ParsedReference]:
    """Parse the TEI returned by GROBID's ``/api/processCitation[List]`` into references.

    Walks every ``biblStruct`` in the document (the citation endpoints emit one per input string)
    and returns a :class:`ParsedReference` for each — empty on malformed/empty TEI.
    """
    if not tei_xml or not tei_xml.strip():
        return []
    try:
        root = etree.fromstring(tei_xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return []
    return [
        _biblstruct_to_reference(bibl, index)
        for index, bibl in enumerate(root.findall(".//t:biblStruct", TEI_NS))
    ]


def parse_tei(tei_xml: str) -> ParsedPaper:
    """Parse GROBID TEI into PaRacORD structures."""
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

    # The paper's own venue/year live in the header's source monograph (the analytic title stays the
    # paper title; the monograph is the journal/conference). GROBID often omits these — that's fine.
    source_bibl = root.find(".//t:teiHeader//t:sourceDesc//t:biblStruct", TEI_NS)
    if source_bibl is not None:
        paper.venue = _text(source_bibl.find(".//t:monogr/t:title", TEI_NS))
        paper.year = _year(
            _first(
                source_bibl.find(".//t:monogr//t:imprint//t:date", TEI_NS),
                source_bibl.find(".//t:monogr//t:date", TEI_NS),
            )
        )

    for pers in root.findall(".//t:teiHeader//t:sourceDesc//t:author/t:persName", TEI_NS):
        forenames = " ".join(_text(f) or "" for f in pers.findall("t:forename", TEI_NS)).strip()
        surname = _text(pers.find("t:surname", TEI_NS)) or ""
        name = " ".join(part for part in (forenames, surname) if part).strip()
        if name:
            paper.authors.append(name)

    for index, bibl in enumerate(root.findall(".//t:listBibl/t:biblStruct", TEI_NS)):
        reference = _biblstruct_to_reference(bibl, index)
        if reference.raw_citation or reference.title or reference.doi:
            paper.references.append(reference)

    for ref in root.findall('.//t:text//t:body//t:ref[@type="bibr"]', TEI_NS):
        target = (ref.get("target") or "").strip()
        reference_key = target.removeprefix("#")
        if not reference_key:
            continue
        boxes = _parse_coords(ref.get("coords"))
        mention = ParsedCitationMention(
            reference_key=reference_key,
            marker_text=_text(ref),
            section_label=_section_label(ref),
            context_before=_neighbor_sentence(ref, previous=True),
            context_sentence=_context_sentence(ref),
            context_after=_neighbor_sentence(ref, previous=False),
            page=boxes[0]["page"] if boxes else None,
            pdf_coordinates=boxes,
        )
        paper.citation_mentions.append(mention)

    return paper


def _parse_coords(coords: str | None) -> list[dict[str, float | int]]:
    """Parse a GROBID ``coords`` attribute into coordinate boxes.

    GROBID emits ``"page,x,y,width,height"`` per box, with multiple boxes separated by
    ``;`` (a mention that wraps across lines). Malformed boxes are skipped.
    """
    if not coords:
        return []
    boxes: list[dict[str, float | int]] = []
    for raw_box in coords.split(";"):
        parts = [part.strip() for part in raw_box.split(",")]
        if len(parts) != 5:
            continue
        try:
            page = int(float(parts[0]))
            x, y, w, h = (float(value) for value in parts[1:])
        except ValueError:
            continue
        boxes.append({"page": page, "x": x, "y": y, "w": w, "h": h})
    return boxes


def _context_sentence(element) -> str | None:
    """Text of the sentence (``<s>``) containing ``element``, or its enclosing paragraph if
    GROBID didn't segment sentences."""
    sentence = _ancestor(element, "s")
    if sentence is not None:
        return _text(sentence)
    paragraph = _ancestor(element, "p")
    return _text(paragraph)


def _neighbor_sentence(element, *, previous: bool) -> str | None:
    """Text of the previous/next sibling ``<s>`` sentence to ``element``'s enclosing sentence,
    skipping any non-``<s>`` siblings in between (e.g. inline markup)."""
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
