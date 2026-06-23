"""Parser for GROBID TEI XML.

Keep raw TEI in storage. This module extracts normalized records without treating TEI-to-JSON
conversion as lossless.
"""

from dataclasses import dataclass, field


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
    references: list[ParsedReference] = field(default_factory=list)


def parse_tei(tei_xml: str) -> ParsedPaper:
    """Parse GROBID TEI into PaperRacks structures."""
    _ = tei_xml
    return ParsedPaper()
