"""Landing-page PDF link discovery (UX batch 3) — pure extraction + publisher rewrites."""

from app.services.pdf_link_finder import find_pdf_links, publisher_pdf_urls

BASE = "https://publisher.example/article/10.1/abc"


def test_citation_pdf_url_meta_wins() -> None:
    html = """
    <html><head>
      <meta name="citation_title" content="A Paper">
      <meta name="citation_pdf_url" content="/content/pdf/10.1/abc.pdf">
    </head><body><a href="/random">random</a></body></html>
    """
    links = find_pdf_links(html, BASE)
    assert links[0] == "https://publisher.example/content/pdf/10.1/abc.pdf"


def test_link_rel_alternate_pdf() -> None:
    html = '<link rel="alternate" type="application/pdf" href="https://cdn.example/p.pdf">'
    assert find_pdf_links(html, BASE) == ["https://cdn.example/p.pdf"]


def test_ieee_style_download_button_anchor() -> None:
    # The owner's motivating example: class carries "pdf" + "downloadPdf" hints.
    html = """
    <a class="xpl-btn-pdf stats-document-lh-action-downloadPdf_2 pdf"
       href="/stamp/stamp.jsp?tp=&arnumber=123">PDF</a>
    <a href="/about">About</a>
    """
    links = find_pdf_links(html, "https://ieeexplore.ieee.org/document/123")
    assert links == ["https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=123"]


def test_download_pdf_text_anchor_scores_and_supplement_is_penalized() -> None:
    html = """
    <a href="/files/main">Download PDF</a>
    <a href="/files/supplementary.pdf">Supplementary PDF</a>
    """
    links = find_pdf_links(html, BASE)
    assert links == ["https://publisher.example/files/main"]


def test_non_http_and_duplicate_links_are_dropped() -> None:
    html = """
    <meta name="citation_pdf_url" content="ftp://bad.example/x.pdf">
    <a href="/a.pdf">PDF</a>
    <a href="/a.pdf">PDF</a>
    """
    assert find_pdf_links(html, BASE) == ["https://publisher.example/a.pdf"]


def test_mangled_html_does_not_raise() -> None:
    assert find_pdf_links("<a href='/x.pdf' <broken", BASE) in (
        [],
        ["https://publisher.example/x.pdf"],
    )


def test_json_sniff_finds_pdf_urls_in_script_blobs() -> None:
    """Layer 4: SPA pages embed the PDF URL in JSON state — no JS execution needed."""
    html = """
    <script type="application/json">{"props":{"pdfUrl":"https:\\/\\/cdn.example\\/full\\/paper.pdf?token=1"}}</script>
    """
    assert find_pdf_links(html, BASE) == ["https://cdn.example/full/paper.pdf?token=1"]


def test_json_sniff_reconstructs_sciencedirect_pdfft_url() -> None:
    html = """
    <script type="application/json">
    {"article":{"pdfDownload":{"urlMetadata":{"path":"science/article/pii","pii":"S0004370224001234",
      "pdfExtension":"/pdfft","queryParams":{"md5":"abc123","pid":"1-s2.0-main.pdf"}}}}}
    </script>
    """
    links = find_pdf_links(
        html, "https://www.sciencedirect.com/science/article/pii/S0004370224001234"
    )
    assert (
        "https://www.sciencedirect.com/science/article/pii/S0004370224001234/pdfft"
        "?md5=abc123&pid=1-s2.0-main.pdf" in links
    )


def test_json_sniff_penalized_urls_are_skipped() -> None:
    html = '<script>{"x":"https://cdn.example/supplementary/extra.pdf"}</script>'
    assert find_pdf_links(html, BASE) == []


def test_publisher_rewrites() -> None:
    assert publisher_pdf_urls("https://arxiv.org/abs/2101.00001") == [
        "https://arxiv.org/pdf/2101.00001"
    ]
    assert publisher_pdf_urls("https://dl.acm.org/doi/10.1145/3292500") == [
        "https://dl.acm.org/doi/pdf/10.1145/3292500"
    ]
    assert publisher_pdf_urls("https://link.springer.com/article/10.1007/s1-2-3") == [
        "https://link.springer.com/content/pdf/10.1007/s1-2-3.pdf"
    ]
    assert publisher_pdf_urls("https://onlinelibrary.wiley.com/doi/10.1002/abc") == [
        "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/abc"
    ]
    assert publisher_pdf_urls("https://ieeexplore.ieee.org/document/123456") == [
        "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=123456"
    ]
    assert publisher_pdf_urls("https://www.nature.com/articles/s41586-1") == [
        "https://www.nature.com/articles/s41586-1.pdf"
    ]
    assert publisher_pdf_urls("https://aclanthology.org/2020.acl-main.1/") == [
        "https://aclanthology.org/2020.acl-main.1.pdf"
    ]
    assert publisher_pdf_urls("https://openreview.net/forum?id=xyz") == [
        "https://openreview.net/pdf?id=xyz"
    ]
    # No rewrite for an unknown host.
    assert publisher_pdf_urls("https://random.example/paper") == []
