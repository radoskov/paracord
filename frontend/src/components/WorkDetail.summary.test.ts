import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Summary, Work } from "../api/client";
import { currentUser } from "../lib/session";
import WorkDetail from "./WorkDetail.svelte";

function makeWork(overrides: Partial<Work> = {}): Work {
  return {
    id: "w1",
    canonical_title: "Attention Is All You Need",
    abstract: "transformers and attention",
    doi: null,
    arxiv_id: null,
    venue: null,
    year: 2017,
    reading_status: "unread",
    canonical_metadata_source: null,
    confirmed_fields: [],
    keywords: [],
    topics: [],
    created_by_user_id: null,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    ...overrides,
  } as unknown as Work;
}

function makeSummary(overrides: Partial<Summary> = {}): Summary {
  return {
    id: "s1",
    entity_type: "work",
    entity_id: "w1",
    summary_type: "local_llm",
    text: "A short summary.",
    model_name: "tier1-extractive-frequency",
    prompt_version: "local-llm-v1",
    created_at: "2024-01-01T00:00:00Z",
    ...overrides,
  } as unknown as Summary;
}

function makeClient(
  summaries: Summary[],
  overrides: Record<string, unknown> = {},
) {
  return {
    listWorkMetadata: vi.fn().mockResolvedValue([]),
    listWorkFiles: vi.fn().mockResolvedValue([]),
    listCitationContexts: vi.fn().mockResolvedValue([]),
    listWorkReferences: vi.fn().mockResolvedValue([]),
    listAnnotations: vi.fn().mockResolvedValue([]),
    listSummaries: vi.fn().mockResolvedValue(summaries),
    listTags: vi.fn().mockResolvedValue([]),
    listWorkTags: vi.fn().mockResolvedValue([]),
    ...overrides,
  };
}

describe("WorkDetail summary provider-fallback indicator (Phase B2)", () => {
  beforeEach(() => {
    currentUser.set({ id: "u1", username: "ed", role: "editor" } as never);
  });

  it("shows the extractive-fallback hint when the summary degraded", async () => {
    const client = makeClient([
      makeSummary({ fallback: true, provider_used: "extractive" }),
    ]);
    render(WorkDetail, { client: client as never, work: makeWork() });
    expect(
      await screen.findByText(
        "Summarized with the extractive fallback (LLM unavailable).",
      ),
    ).toBeTruthy();
  });

  it("stays quiet when the summary did not degrade", async () => {
    const client = makeClient([
      makeSummary({ fallback: false, summary_type: "extractive" }),
    ]);
    render(WorkDetail, { client: client as never, work: makeWork() });
    // Let the async load settle so the summaries block has rendered.
    await screen.findByText("A short summary.");
    expect(
      screen.queryByText(
        "Summarized with the extractive fallback (LLM unavailable).",
      ),
    ).toBeNull();
  });

  it("badges an un-extracted local-agent stub as 'not extracted' (B6)", async () => {
    const client = makeClient([]);
    render(WorkDetail, {
      client: client as never,
      work: makeWork({
        canonical_metadata_source: "agent_index_only",
      } as never),
    });
    expect(await screen.findByText("not extracted")).toBeTruthy();
  });

  it("generates a short summary from the paper view and shows it (B8)", async () => {
    const createSummary = vi.fn().mockResolvedValue(makeSummary());
    // No summary at first; after generating, the reload returns one.
    const listSummaries = vi
      .fn()
      .mockResolvedValueOnce([])
      .mockResolvedValue([makeSummary()]);
    const client = makeClient([], { createSummary, listSummaries });
    render(WorkDetail, { client: client as never, work: makeWork() });

    // The Short summary sub-panel's action is "Summarize" until one exists.
    const btn = await screen.findByRole("button", { name: /^summarize$/i });
    await fireEvent.click(btn);

    expect(createSummary).toHaveBeenCalledWith("w1", "auto", "short");
    await waitFor(() =>
      expect(screen.getByText("A short summary.")).toBeTruthy(),
    );
    // Now that a short summary exists, its button becomes "Regenerate".
    await screen.findByRole("button", { name: /^regenerate$/i });
  });

  it("generates a detailed summary separately from the short one", async () => {
    const detailed = makeSummary({
      id: "s2",
      summary_type: "local_llm_detailed",
      text: "Intro paragraph.\n\nSection one.\n\nSection two.",
    });
    const createSummary = vi.fn().mockResolvedValue(detailed);
    const listSummaries = vi.fn().mockResolvedValueOnce([]).mockResolvedValue([detailed]);
    const client = makeClient([], { createSummary, listSummaries });
    render(WorkDetail, { client: client as never, work: makeWork() });

    await fireEvent.click(await screen.findByRole("button", { name: /generate detailed/i }));
    expect(createSummary).toHaveBeenCalledWith("w1", "auto", "detailed");
    await waitFor(() => expect(screen.getByText("Intro paragraph.")).toBeTruthy());
    expect(screen.getByText("Section two.")).toBeTruthy();
  });
});
