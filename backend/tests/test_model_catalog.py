"""Model catalog: VRAM math, name-param parsing, and search filtering/ranking (no network).

All cases pass ``allow_scrape=False`` so the curated catalog is exercised deterministically without
touching ollama.com; the live-scrape path is best-effort and covered by its own fallback contract."""

from app.services import model_catalog as mc


def test_estimate_vram_scales_with_size_and_quant() -> None:
    # A 4B Q4_K_M model lands in the ~3-4 GB range (weights + KV + overhead).
    v = mc.estimate_vram_gb(4.0, "Q4_K_M")
    assert 3.0 <= v <= 4.0
    # Heavier quant → more VRAM; a bigger model → more still.
    assert mc.estimate_vram_gb(4.0, "Q8_0") > v
    assert mc.estimate_vram_gb(8.0, "Q4_K_M") > v
    # Unknown quant falls back to the Q4_K_M byte rate (no crash).
    assert mc.estimate_vram_gb(4.0, "bogus") == v


def test_params_from_name() -> None:
    assert mc.params_from_name("qwen3:4b") == 4.0
    assert mc.params_from_name("llama3.2:1b") == 1.0
    assert mc.params_from_name("phi3.5") is None  # 3.5 has no trailing 'b'
    assert mc.params_from_name("nomic-embed-text") is None


def test_search_filters_and_ranks_by_popularity() -> None:
    res = mc.search_models("qwen", allow_scrape=False)
    assert res and all("qwen" in r["name"] for r in res)
    # Popularity-sorted, descending.
    pops = [r["popularity"] for r in res]
    assert pops == sorted(pops, reverse=True)
    # Every LLM hit carries a VRAM estimate.
    assert all(r["vram_gb"] is not None for r in res if r["params_b"])


def test_search_blank_returns_whole_catalog() -> None:
    assert len(mc.search_models("", allow_scrape=False)) == len(mc._CATALOG)


def test_search_marks_pulled_models() -> None:
    # /api/tags names carry ':latest'; an untagged catalog name must still match.
    res = mc.search_models("embed", local_names=["nomic-embed-text:latest"], allow_scrape=False)
    by_name = {r["name"]: r for r in res}
    assert by_name["nomic-embed-text"]["pulled"] is True
    assert by_name["mxbai-embed-large"]["pulled"] is False


def test_scrape_failure_falls_back_to_catalog(monkeypatch) -> None:
    # Any scrape error must not break search — it returns the curated matches only.
    def _boom(query, *, timeout=6.0):
        raise RuntimeError("no egress")

    monkeypatch.setattr(mc, "_scrape_ollama_library", _boom)
    # _scrape_ollama_library itself suppresses exceptions, but even a raising stub must be tolerated
    # by the caller — assert search still yields the catalog matches.
    res = mc.search_models("qwen", allow_scrape=True)
    assert res and all(r["source"] == "catalog" for r in res)
