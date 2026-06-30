"""Deterministic keyword extraction + text-layer/OCR signal (SPEC §8.15.1 / §8.3)."""

from app.services.extraction import _text_layer_quality
from app.services.keyword_extraction import extract_keywords


def test_extract_keywords_is_deterministic_and_salient():
    text = (
        "Transformer models use self-attention. Self-attention lets transformer models capture "
        "long-range dependencies. Attention is computed over token sequences."
    )
    kws = extract_keywords(text, top_k=5)
    assert kws == extract_keywords(text, top_k=5)  # deterministic
    joined = " ".join(kws).lower()
    assert "transformer models" in joined or "self-attention" in joined or "attention" in joined
    assert "the" not in kws  # stop words excluded


def test_extract_keywords_empty():
    assert extract_keywords("") == []
    assert extract_keywords("the and of to") == []  # all stop words


def test_text_layer_quality_flags_scanned_pdf():
    assert _text_layer_quality("", None, 10) == "poor"  # no text on a 10-page PDF → needs OCR
    assert _text_layer_quality("x" * 5000, "abstract here", 5) == "good"
