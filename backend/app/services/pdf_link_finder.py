"""Landing-page PDF link discovery (UX batch 3 — "make the download attempt less weak").

Find-on-web / import downloads used to succeed only when a source handed us a direct PDF URL; a
publisher *landing page* (HTML) failed with ``manual_upload_needed`` even when the page carries an
obvious "Download PDF" button. This module automates exactly the clicks a user would make for a
single requested paper — it is not a crawler: everything is bounded (one landing page, a handful of
candidate links) and every URL the caller actually fetches still passes the same denylist / SSRF /
download-policy gates as before.

Four layers, cheapest first:

1. :func:`publisher_pdf_urls` — deterministic URL rewrites for major publishers (ACM, Springer,
   Wiley, IEEE, Nature, MDPI, arXiv, ACL, OpenReview, …). No page fetch needed.
2. HTML metadata — ``<meta name="citation_pdf_url">`` (the Highwire tag most publishers emit for
   Google Scholar) and ``<link rel="alternate" type="application/pdf">``.
3. Scored anchor heuristics — ``<a>`` elements whose href/class/id/text signal a PDF download
   (e.g. IEEE's ``xpl-btn-pdf`` / ``stats-document-lh-action-downloadPdf_2``), with penalties for
   supplements/samples so we don't grab the wrong file.
4. Embedded-JSON sniffing — "JS-only" pages usually render from a JSON blob embedded in the
   initial HTML. We scan ``<script>`` bodies for PDF-ish URLs and reconstruct ScienceDirect's
   ``/pii/{PII}/pdfft?md5=…&pid=…`` download URL from its ``urlMetadata`` state object, without
   executing any JavaScript.

Pure functions (no network, no DB) so they are unit-testable; the caller does all fetching.
"""

from __future__ import annotations

import contextlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

# How many extracted links a caller should reasonably try (kept small on purpose — this automates
# one user's click, not a scrape).
MAX_LINKS = 5

_PDF_HREF = re.compile(r"\.pdf($|[?#])|/pdf(/|$|\?)|[?&]type=printable", re.IGNORECASE)
_PDF_HINT = re.compile(r"pdf", re.IGNORECASE)
_DOWNLOAD_HINT = re.compile(r"download|full[-_ ]?text|fulltext", re.IGNORECASE)
_TEXT_PDF = re.compile(r"\b(download|view|get|full[- ]?text)?\s*(article\s+)?pdf\b", re.IGNORECASE)
# Things that look like a PDF link but are the WRONG file for "the paper itself".
_PENALTY = re.compile(
    r"supplement|suppl|appendix|sample|preview|toc|front[-_ ]?matter|cover|erratum|correction"
    r"|checklist|permission|citation|bibtex|ris\b|epub|slides",
    re.IGNORECASE,
)


def publisher_pdf_urls(url: str) -> list[str]:
    """Deterministic PDF-URL rewrites for major publishers, given an article landing URL.

    Returns candidate URLs (possibly empty), most likely first. Callers must still classify each
    against the download policy before fetching.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    out: list[str] = []

    def _add(u: str) -> None:
        if u != url and u not in out:
            out.append(u)

    if host.endswith("arxiv.org") and "/abs/" in path:
        _add(url.replace("/abs/", "/pdf/", 1))
    if host.endswith("dl.acm.org") and "/doi/" in path and "/doi/pdf" not in path:
        _add(url.replace("/doi/", "/doi/pdf/", 1))
    if host.endswith("link.springer.com") and "/article/" in path:
        _add(url.replace("/article/", "/content/pdf/", 1).rstrip("/") + ".pdf")
    if host.endswith("onlinelibrary.wiley.com") and "/doi/" in path and "/pdf" not in path:
        _add(url.replace("/doi/", "/doi/pdfdirect/", 1))
    if host.endswith("ieeexplore.ieee.org"):
        m = re.search(r"/document/(\d+)", path)
        if m:
            _add(f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={m.group(1)}")
    if host.endswith("nature.com") and "/articles/" in path and not path.endswith(".pdf"):
        _add(url.split("?")[0].rstrip("/") + ".pdf")
    if host.endswith("mdpi.com") and re.search(r"/\d{4}-\d{3,4}/", path) and not path.endswith(
        ("/pdf", ".pdf")
    ):
        _add(url.split("?")[0].rstrip("/") + "/pdf")
    if host.endswith("aclanthology.org") and not path.endswith(".pdf"):
        _add(url.split("?")[0].rstrip("/") + ".pdf")
    if host.endswith("openreview.net") and "/forum" in path:
        _add(url.replace("/forum", "/pdf", 1))
    if (
        (host.endswith("biorxiv.org") or host.endswith("medrxiv.org"))
        and "/content/" in path
        and not path.endswith(".full.pdf")
    ):
        _add(url.split("?")[0].rstrip("/") + ".full.pdf")
    if host.endswith("journals.plos.org") and "article" in path and "id=" in (parsed.query or ""):
        _add(
            f"{parsed.scheme}://{parsed.netloc}{path.replace('/article', '/article/file')}"
            f"?{parsed.query}&type=printable"
        )
    return out[:MAX_LINKS]


# Cap on the total <script> text retained for JSON sniffing (SPA state blobs are big but bounded).
_MAX_SCRIPT_BYTES = 3 * 1024 * 1024
# PDF-ish URLs inside script text (after \/ unescaping): absolute, ending .pdf or a pdfft route.
_SCRIPT_PDF_URL = re.compile(
    r"https?://[^\s\"'<>\\]+?(?:\.pdf(?:[?#][^\s\"'<>\\]*)?|/pdfft\?[^\s\"'<>\\]*)",
    re.IGNORECASE,
)


class _PdfLinkParser(HTMLParser):
    """Collect citation_pdf_url metas, alternate-PDF links, scored anchors and <script> bodies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta_urls: list[str] = []
        self.alt_urls: list[str] = []
        # (score, order, href) for anchors — text arrives via handle_data while an <a> is open.
        self.anchors: list[list] = []  # [score, order, href, text_matched]
        self._open_anchor: list | None = None
        self._order = 0
        self.scripts: list[str] = []
        self._script_bytes = 0
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script":
            self._in_script = self._script_bytes < _MAX_SCRIPT_BYTES
            if self._in_script:
                self.scripts.append("")
            return
        if tag == "meta":
            name = a.get("name", "").lower()
            if name in ("citation_pdf_url", "eprints.document_url") and a.get("content"):
                self.meta_urls.append(a["content"])
            return
        if tag == "link":
            rel = a.get("rel", "").lower()
            typ = a.get("type", "").lower()
            if "alternate" in rel and typ == "application/pdf" and a.get("href"):
                self.alt_urls.append(a["href"])
            return
        if tag == "a":
            href = a.get("href", "")
            if not href or href.startswith(("javascript:", "mailto:", "#")):
                self._open_anchor = None
                return
            blob = " ".join(
                (a.get("class", ""), a.get("id", ""), a.get("aria-label", ""), a.get("title", ""))
            )
            score = 0
            if _PDF_HREF.search(href):
                score += 3
            if _PDF_HINT.search(blob):
                score += 2
            if _DOWNLOAD_HINT.search(blob):
                score += 2
            if a.get("type", "").lower() == "application/pdf":
                score += 3
            if _PENALTY.search(href) or _PENALTY.search(blob):
                score -= 6
            self._order += 1
            self._open_anchor = [score, self._order, href, False]

    def handle_data(self, data: str) -> None:
        if self._in_script:
            if self._script_bytes < _MAX_SCRIPT_BYTES and self.scripts:
                self.scripts[-1] += data
                self._script_bytes += len(data)
            return
        anchor = self._open_anchor
        if anchor is None or anchor[3] or not data.strip():
            return
        if _TEXT_PDF.search(data):
            anchor[0] += 3
            anchor[3] = True
        if _PENALTY.search(data):
            anchor[0] -= 6

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False
            return
        if tag == "a" and self._open_anchor is not None:
            self.anchors.append(self._open_anchor)
            self._open_anchor = None


def _sciencedirect_pdfft_urls(value, out: list[str]) -> None:
    """Recursively reconstruct ScienceDirect ``/pii/{PII}/pdfft?md5=…&pid=…`` URLs from the
    ``urlMetadata``-shaped objects embedded in the page's JSON state."""
    if isinstance(value, dict):
        pii = value.get("pii")
        params = value.get("queryParams")
        if isinstance(pii, str) and isinstance(params, dict):
            md5 = params.get("md5")
            pid = params.get("pid")
            if md5 and pid:
                path = str(value.get("path") or "science/article/pii").strip("/")
                ext = str(value.get("pdfExtension") or "/pdfft")
                out.append(
                    f"https://www.sciencedirect.com/{path}/{pii}{ext}?md5={md5}&pid={pid}"
                )
        for child in value.values():
            _sciencedirect_pdfft_urls(child, out)
    elif isinstance(value, list):
        for child in value:
            _sciencedirect_pdfft_urls(child, out)


def _pdf_urls_from_scripts(scripts: list[str]) -> list[str]:
    """Layer 4: PDF-ish URLs found in <script> bodies (JSON state blobs), best-effort."""
    out: list[str] = []
    for raw in scripts:
        if not raw.strip():
            continue
        text = raw.replace("\\/", "/")
        for m in _SCRIPT_PDF_URL.finditer(text):
            url = m.group(0)
            if not _PENALTY.search(url):
                out.append(url)
        stripped = text.lstrip()
        if stripped[:1] in ("{", "["):
            # Not JSON (or truncated) → the regex pass above was enough.
            with contextlib.suppress(Exception):
                _sciencedirect_pdfft_urls(json.loads(stripped), out)
    return out


def find_pdf_links(html: str, base_url: str) -> list[str]:
    """Extract likely article-PDF URLs from a landing page, best first (≤ :data:`MAX_LINKS`).

    Metadata beats heuristics: ``citation_pdf_url`` first, then ``<link rel=alternate>``, then
    anchors scoring ≥ 3 (a bare '.pdf' href alone qualifies; class/text signals raise it; the
    supplement/sample penalty sinks wrong files). Relative hrefs resolve against ``base_url``;
    only http(s) results are returned.
    """
    parser = _PdfLinkParser()
    # A mangled page yields whatever was parsed up to the failure point.
    with contextlib.suppress(Exception):
        parser.feed(html)
    ordered: list[str] = list(parser.meta_urls) + list(parser.alt_urls)
    scored = [a for a in parser.anchors if a[0] >= 3]
    scored.sort(key=lambda a: (-a[0], a[1]))
    ordered += [a[2] for a in scored]
    # Layer 4 (embedded JSON) ranks after real anchors — it's the fallback for SPA pages whose
    # download button doesn't exist in the initial HTML at all.
    ordered += _pdf_urls_from_scripts(parser.scripts)

    out: list[str] = []
    seen: set[str] = set()
    for raw in ordered:
        absolute = urljoin(base_url, raw.strip())
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        if absolute in seen or absolute == base_url:
            continue
        seen.add(absolute)
        out.append(absolute)
        if len(out) >= MAX_LINKS:
            break
    return out
