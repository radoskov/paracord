import { render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import type { AiStatus } from "../api/client";
import AiModelsPanel from "./AiModelsPanel.svelte";

// A minimal getAiStatus fixture; overrides let a test flip the topic backend / availability.
function makeStatus(overrides: Partial<AiStatus> = {}): AiStatus {
  return {
    config: {
      embedding_provider: "hash_bow",
      embedding_model: null,
      summary_provider: "extractive",
      summary_model: "llama3",
      topic_backend: "tfidf",
      topic_embedding_model: null,
      ocr_backend: "ocrmypdf",
      ollama_url: "http://localhost:11434",
      vram_budget_gb: null,
      query_cache_size: 2048,
      auto_unmount: true,
      auto_unmount_minutes: 5,
      summary_llm_timeout: 120,
      summary_reasoning: false,
    },
    allowed: {
      embedding_provider: ["hash_bow", "sentence_transformers", "ollama"],
      summary_provider: ["extractive", "local_llm"],
      topic_backend: ["tfidf", "embedding", "bertopic"],
      ocr_backend: ["none", "ocrmypdf", "pymupdf"],
    },
    providers: {
      embedding: {
        hash_bow: { available: true, note: "Default, dependency-free." },
        ollama: {
          available: false,
          note: "Start the Ollama profile (make up-ai) and set its URL.",
        },
      },
      summary: {
        extractive: { available: true, note: "Default, dependency-free." },
      },
      topic: {
        tfidf: { available: true, note: "Default, dependency-free." },
        embedding: {
          available: true,
          note: "Built-in deterministic TF-IDF clustering (does not use embedding vectors yet).",
        },
        bertopic: {
          available: true,
          note: "BERTopic is not installed — using the built-in deterministic TF-IDF topic model (same results as 'tfidf', with richer metadata).",
        },
      },
      extraction: {
        none: {
          available: true,
          note: "OCR pre-step disabled — GROBID runs on the PDF as-is.",
        },
        ocrmypdf: { available: true, note: null },
        pymupdf: {
          available: false,
          note: "PyMuPDF (fitz) + tesseract not found in this image — rebuild the base image (bundles PyMuPDF + tesseract-ocr).",
        },
        grobid: {
          available: true,
          note: "Default TEI extractor (GROBID service).",
        },
      },
      ollama_reachable: false,
    },
    reindex: { model_name: "hash-bow-v1", indexed: 3, total: 5 },
    ollama_reachable: false,
    bertopic_installed: false,
    sentence_transformers_installed: false,
    active: {
      embedding: {
        selected: "hash_bow",
        available: true,
        note: "Default, dependency-free.",
      },
      summary: {
        selected: "extractive",
        available: true,
        note: "Default, dependency-free.",
      },
      topic: {
        selected: "tfidf",
        available: true,
        note: "Default, dependency-free.",
      },
      extraction: { selected: "ocrmypdf", available: true, note: null },
    },
    ...overrides,
  };
}

function makeClient(status: AiStatus) {
  return {
    getAiStatus: vi.fn().mockResolvedValue(status),
    listAiModels: vi.fn().mockResolvedValue({ models: [] }),
  };
}

describe("AiModelsPanel", () => {
  it("renders a capability card per feature with what-it-does + used-for help", async () => {
    const client = makeClient(makeStatus());
    render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getAiStatus).toHaveBeenCalled());

    expect(
      await screen.findByText("Semantic search & related papers"),
    ).toBeTruthy();
    expect(screen.getByText("Topic modeling")).toBeTruthy();
    expect(screen.getByText("Scope summaries")).toBeTruthy();
    expect(screen.getByText("Keyword extraction")).toBeTruthy();
    // The used-for help line renders.
    expect(screen.getByText(/Used for: the semantic search box/i)).toBeTruthy();
    // A status badge with a reason renders (green baseline for the always-on hash-BOW default).
    expect(screen.getAllByText("Built-in baseline").length).toBeGreaterThan(0);
  });

  it("shows the BERTopic honesty banner when the topic backend pretends to be advanced", async () => {
    const status = makeStatus();
    status.config.topic_backend = "bertopic";
    const client = makeClient(status);
    render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getAiStatus).toHaveBeenCalled());

    expect(
      await screen.findByText(
        /BERTopic isn't installed; this uses the built-in TF-IDF topic model/i,
      ),
    ).toBeTruthy();
  });

  it("surfaces the reason when the active embedding provider is unavailable", async () => {
    const status = makeStatus();
    status.config.embedding_provider = "ollama";
    status.active.embedding = {
      selected: "ollama",
      available: false,
      note: "Start the Ollama profile (make up-ai) and set its URL.",
    };
    const client = makeClient(status);
    render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getAiStatus).toHaveBeenCalled());

    // The red "falls back" badge + its reason are shown.
    expect(await screen.findByText("Falls back to hash-BOW")).toBeTruthy();
    expect(
      screen.getAllByText(/Start the Ollama profile/i).length,
    ).toBeGreaterThan(0);
  });

  it("opens a Help dialog explaining the options (incl. the embedding pre-filter)", async () => {
    const client = makeClient(makeStatus());
    render(AiModelsPanel, { client: client as never });
    const { fireEvent } = await import("@testing-library/svelte");
    await fireEvent.click(await screen.findByRole("button", { name: /Help/ }));
    expect(await screen.findByText(/The five capabilities/i)).toBeTruthy();
    expect(screen.getByText(/Embedding pre-filter/i)).toBeTruthy();
  });

  it("renders the PDF text extraction / OCR card", async () => {
    const client = makeClient(makeStatus());
    render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getAiStatus).toHaveBeenCalled());

    expect(await screen.findByText("PDF text extraction / OCR")).toBeTruthy();
    expect(
      screen.getByText(/Adds a searchable text layer to scanned/i),
    ).toBeTruthy();
  });

  it("shows an OCR-unavailable reason (no install button) when the selected backend is missing", async () => {
    const status = makeStatus();
    status.config.ocr_backend = "pymupdf";
    status.active.extraction = {
      selected: "pymupdf",
      available: false,
      note: "PyMuPDF (fitz) + tesseract not found in this image — rebuild the base image (bundles PyMuPDF + tesseract-ocr).",
    };
    const client = makeClient(status);
    render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getAiStatus).toHaveBeenCalled());

    // The reason names the rebuild path and there is NO pip/install button (backends are image-built).
    expect(
      (await screen.findAllByText(/rebuild the base image/i)).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /install/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /pip/i })).toBeNull();
  });
});

describe("AiModelsPanel mount/unmount (#5)", () => {
  it("mounts the selected embedding model as a background job (with compute)", async () => {
    const status = makeStatus();
    status.config.embedding_provider = "ollama";
    status.config.embedding_model = "nomic-embed-text";
    const mountAiModel = vi.fn().mockResolvedValue({ job_id: "mount-job", status: "queued" });
    const client = {
      getAiStatus: vi.fn().mockResolvedValue(status),
      listAiModels: vi.fn().mockResolvedValue({
        models: [{ provider: "ollama", name: "nomic-embed-text", size_bytes: null, vram_gb: 1 }],
      }),
      getLoadedModels: vi.fn().mockResolvedValue({ loaded: [], vram_budget_gb: null }),
      // runningAiJobs reads getJobs (no in-flight AI jobs); pollModelJob polls getJobResult by id.
      getJobs: vi.fn().mockResolvedValue({ jobs: [] }),
      getJobResult: vi.fn().mockResolvedValue({ status: "finished" }),
      mountAiModel,
      validateAiModel: vi.fn().mockResolvedValue({
        present: true,
        embeddings: true,
        canonical: "nomic-embed-text",
        error: null,
      }),
    };
    const { getByRole } = render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getLoadedModels).toHaveBeenCalled());
    await (
      await import("@testing-library/svelte")
    ).fireEvent.click(getByRole("button", { name: /^Mount$/ }));
    // Enqueued with the default 'auto' compute.
    await waitFor(() =>
      expect(mountAiModel).toHaveBeenCalledWith("nomic-embed-text", "embedding", "auto"),
    );
  });

  it("shows a loaded model with an Unmount control", async () => {
    const status = makeStatus();
    status.config.embedding_provider = "ollama";
    status.config.embedding_model = "nomic-embed-text";
    const client = {
      getAiStatus: vi.fn().mockResolvedValue(status),
      listAiModels: vi.fn().mockResolvedValue({ models: [] }),
      getLoadedModels: vi.fn().mockResolvedValue({
        loaded: [
          { name: "nomic-embed-text:latest", size_bytes: 3e8, size_vram_bytes: 0 },
        ],
        vram_budget_gb: 8,
      }),
      getJobs: vi.fn().mockResolvedValue({ jobs: [] }),
      validateAiModel: vi.fn().mockResolvedValue({
        present: true,
        embeddings: true,
        canonical: "nomic-embed-text",
        error: null,
      }),
    };
    render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getLoadedModels).toHaveBeenCalled());
    // The loaded-in-memory list renders the model name.
    expect(await screen.findByText("nomic-embed-text:latest")).toBeTruthy();
    // At least one Unmount button is present (loaded list + the card badge).
    expect(screen.getAllByRole("button", { name: /Unmount/ }).length).toBeGreaterThan(0);
  });

  it("flips the card's Mount button to Unmount once the mount job updates the loaded list", async () => {
    // Regression: the card button read isLoaded(config.x) — a function call Svelte only re-evaluated
    // when config changed, so after a mount job finished (which updates `loaded`, not `config`) the
    // button stayed "Mount" until the model dropdown was touched. It must now react to `loaded`.
    const status = makeStatus();
    status.config.embedding_provider = "ollama";
    status.config.embedding_model = "nomic-embed-text";
    const getLoadedModels = vi
      .fn()
      .mockResolvedValueOnce({ loaded: [], vram_budget_gb: null }) // initial load: not mounted
      .mockResolvedValue({
        loaded: [{ name: "nomic-embed-text:latest", size_bytes: 3e8, size_vram_bytes: 3e8 }],
        vram_budget_gb: null,
      }); // after the mount job's refreshLive: mounted
    const client = {
      getAiStatus: vi.fn().mockResolvedValue(status),
      listAiModels: vi.fn().mockResolvedValue({
        models: [{ provider: "ollama", name: "nomic-embed-text", size_bytes: null, vram_gb: 1 }],
      }),
      getLoadedModels,
      getJobs: vi.fn().mockResolvedValue({ jobs: [] }),
      getJobResult: vi.fn().mockResolvedValue({ status: "finished" }),
      mountAiModel: vi.fn().mockResolvedValue({ job_id: "mount-job", status: "queued" }),
      validateAiModel: vi.fn().mockResolvedValue({
        present: true,
        embeddings: true,
        canonical: "nomic-embed-text",
        error: null,
      }),
    };
    const { getByRole } = render(AiModelsPanel, { client: client as never });
    // Initially not mounted → the card shows Mount, no Unmount.
    await waitFor(() => expect(getByRole("button", { name: /^Mount$/ })).toBeTruthy());
    await (
      await import("@testing-library/svelte")
    ).fireEvent.click(getByRole("button", { name: /^Mount$/ }));
    // After the job completes and refreshLive re-reads the loaded list, the button reacts and flips.
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: /Unmount/ }).length).toBeGreaterThan(0),
    );
  });

  it("disables the summary model selector (showing no model) for the extractive provider", async () => {
    const status = makeStatus();
    status.config.summary_provider = "extractive";
    status.config.summary_model = "qwen3.5:4b"; // a stale stored model must NOT be shown as in-use
    const client = {
      getAiStatus: vi.fn().mockResolvedValue(status),
      listAiModels: vi.fn().mockResolvedValue({ models: [] }),
      getLoadedModels: vi.fn().mockResolvedValue({ loaded: [], vram_budget_gb: null }),
      getJobs: vi.fn().mockResolvedValue({ jobs: [] }),
    };
    render(AiModelsPanel, { client: client as never });
    await waitFor(() => expect(client.getAiStatus).toHaveBeenCalled());
    // The confusing model name is gone; the extractive summarizer shows it uses no model.
    expect(await screen.findByText(/the extractive summarizer uses no LLM/i)).toBeTruthy();
    expect(screen.queryByText("qwen3.5:4b")).toBeNull();
  });
});

describe("AiModelsPanel lexical index (B5)", () => {
  it("shows a Rebuild index button that rebuilds the lexical index and refreshes status", async () => {
    const stale = makeStatus({
      lexical_index: { loaded: true, docs: 1, stale: true },
    });
    const fresh = makeStatus({
      lexical_index: { loaded: true, docs: 3, stale: false },
    });
    const getAiStatus = vi
      .fn()
      .mockResolvedValueOnce(stale)
      .mockResolvedValue(fresh);
    const rebuildLexicalIndex = vi
      .fn()
      .mockResolvedValue({ status: "rebuilt", job_id: null });
    const client = {
      getAiStatus,
      listAiModels: vi.fn().mockResolvedValue({ models: [] }),
      rebuildLexicalIndex,
    };
    const { getByRole, findByText } = render(AiModelsPanel, {
      client: client as never,
    });
    await findByText(/rebuilding to include recent changes/i);
    await (
      await import("@testing-library/svelte")
    ).fireEvent.click(getByRole("button", { name: /rebuild index/i }));
    expect(rebuildLexicalIndex).toHaveBeenCalled();
  });
});
