"""Keyword extraction + text-layer/OCR signal (SPEC §8.15.1 / §8.3).

These exercise the YAKE+RAKE fusion path but are written to hold in RAKE-only fallback too (the
YAKE dependency is guarded), so they validate the filtering/trimming/boosting/dedup layers that run
regardless of which scorer feeds them.
"""

from app.services.extraction import _text_layer_quality
from app.services.keyword_extraction import (
    _is_valid_phrase,
    _trim_stopwords,
    build_corpus_idf,
    extract_keywords,
)


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


def test_trim_stopwords_strips_boundary_function_words():
    assert _trim_stopwords("the neural network for") == "neural network"
    assert _trim_stopwords("deep learning") == "deep learning"


def test_is_valid_phrase_rules():
    assert _is_valid_phrase("neural network", 4)
    assert not _is_valid_phrase("a b c d e f", 4)  # too long
    assert not _is_valid_phrase("the of and", 4)  # no content word
    assert not _is_valid_phrase("", 4)


def test_extract_keywords_rejects_overlong_phrases():
    text = (
        "The generative adversarial network training procedure loss function gradient penalty term "
        "stabilizes convergence. Generative adversarial network models synthesize images."
    )
    kws = extract_keywords(text, top_k=8, max_phrase_words=4)
    assert all(len(k.split()) <= 4 for k in kws)


def test_extract_keywords_boost_favours_title_terms():
    # Terms echoed in the boost text (title) should surface more prominently than without it.
    text = (
        "Classical correlation appears in many systems. Quantum entanglement enables quantum "
        "teleportation. Quantum entanglement is a resource for quantum computing."
    )
    boosted = extract_keywords(text, top_k=3, boost_text="Quantum entanglement in computing")
    plain = extract_keywords(text, top_k=3)
    q = lambda ks: sum(("quantum" in k or "entanglement" in k) for k in ks)  # noqa: E731
    assert q(boosted) >= q(plain)
    assert q(boosted) >= 1


def test_extract_keywords_dedupes_near_duplicates():
    text = (
        "Convolutional neural network layers extract features. The convolutional neural network "
        "dominates vision. Convolutional neural networks learn feature hierarchies."
    )
    kws = extract_keywords(text, top_k=10)
    # 'convolutional neural network' vs '...networks' (plural) collapse to one representative.
    conv = [k for k in kws if k.startswith("convolutional neural network")]
    assert len(conv) <= 1


def test_build_corpus_idf_downweights_common_terms():
    idf = build_corpus_idf(["deep learning model", "deep learning system", "quantum computing"])
    # 'deep' appears in 2/3 docs, 'quantum' in 1/3 → quantum is rarer → higher IDF.
    assert idf["quantum"] > idf["deep"]


def test_text_layer_quality_flags_scanned_pdf():
    assert _text_layer_quality("", None, 10) == "poor"  # no text on a 10-page PDF → needs OCR
    assert _text_layer_quality("x" * 5000, "abstract here", 5) == "good"
