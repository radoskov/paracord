# Handoff: AI & Models panel clarity + Help dialog

Frontend-only follow-up to the owner's request to make the AI & Models tab self-explanatory: clearer
copy for the confusing sections, tooltips, and a comprehensive in-app Help dialog.

## Changes (committed, tested)

- **Rewrote confusing sections** (`AiModelsPanel.svelte`):
  - *Registered embedding models*: now explains what "registered" means (a model gets a stored vector
    column the first time you index with it via Save/Mount), that registered models keep their vectors
    so you can switch without re-indexing, what Multimode (RRF) does, and that Delete frees a slot.
  - *Embedding index*: explains vectors, what Reindex does + when to run it (model change / <100 %
    coverage), that it's background and search keeps working meanwhile.
  - Pulled-model rows now also show the estimated memory to run each model.
- **Help dialog** (reuses the shared `Modal.svelte`): a `? Help` button in the card header opens a
  wide, scrollable, collapsible (`<details>`) guide covering: the five capabilities; providers &
  baselines; find/pull/delete; mount/unmount & memory (VRAM budget, GPU/CPU, pinned vs auto);
  registered models & Multimode; embedding index / reindex / chunk-ANN(HNSW) / lexical(BM25F); the
  Recommend parameters (**Embedding pre-filter** — what it consumes and what happens when off — plus
  scoring, parent-combine, K/cap/recompute); and a glossary (embedding, cosine similarity,
  quantization, VRAM, keep_alive, ANN/HNSW, RRF, TF-IDF, BM25, RAKE, GROBID/OCR).
- **More tooltips** on section headings, buttons and the compute selector.

## Verification

`make frontend-test` — 342 passed (+1: opens the Help dialog and shows the pre-filter section).
Dev server healed + warm. No backend changes.

## Notes

Purely additive/copy — no API or schema changes. The pre-filter parameter itself lives on the
Insights → Recommend panel; the Help text documents it here because it depends on the models
configured on this tab.
