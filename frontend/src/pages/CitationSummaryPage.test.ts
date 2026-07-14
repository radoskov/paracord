import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CitationSummary } from "../api/client";
import CitationSummaryPage from "./CitationSummaryPage.svelte";

function baseSummary(over: Partial<CitationSummary> = {}): CitationSummary {
  return {
    scope_work_count: 3,
    coverage_held: 312,
    coverage_total: 503,
    coverage_pct: 62,
    most_cited_local: [
      {
        work_id: "w1",
        title: "A local hub",
        year: 2020,
        doi: "10.1/hub",
        score: 4,
      },
    ],
    most_cited_external: [],
    frequently_cited_missing: [
      {
        key: "doi:10.9/missing",
        title: "Attention Is All You Need",
        doi: "10.9/missing",
        year: 2017,
        cited_by_count: 9,
        mention_count: 12,
        reference_id: "ref-1",
        arxiv_id: "1706.03762",
      },
    ],
    bridge_papers: [],
    isolated_papers: [],
    chronological: [],
    bridge_method: "brandes_betweenness_undirected",
    computed_at: "2026-07-07T00:00:00Z",
    version: "sig",
    notes: [],
  };
}

function makeClient(over: Record<string, unknown> = {}) {
  return {
    listShelves: vi.fn().mockResolvedValue([]),
    listRacks: vi.fn().mockResolvedValue([]),
    listImportBatches: vi.fn().mockResolvedValue([]),
    listSavedFilters: vi.fn().mockResolvedValue([]),
    citationSummary: vi.fn().mockResolvedValue(baseSummary()),
    venueAuthorSummary: vi.fn().mockResolvedValue({
      scope_work_count: 0,
      venues: [],
      authors: [],
      papers_without_venue: 0,
      papers_without_authors: 0,
      distinct_venue_count: 0,
      distinct_author_count: 0,
      notes: [],
    }),
    getWorklist: vi.fn().mockResolvedValue({}),
    externalPreview: vi.fn(),
    setWorklistDecision: vi.fn(),
    clearWorklistDecision: vi.fn(),
    exportMissingWorks: vi.fn(),
    // Never-resolving so the WorkDetail modal never mounts; we only assert the call wiring (C2).
    getWork: vi.fn().mockReturnValue(new Promise(() => {})),
    importReferenceAsWork: vi.fn(),
    ...over,
  };
}

async function summarize(): Promise<void> {
  await fireEvent.click(screen.getByTestId("summary-build"));
}

describe("CitationSummaryPage enrichments (Track C)", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn().mockReturnValue("blob:x"),
      revokeObjectURL: vi.fn(),
    });
  });

  it("shows the library-coverage headline (C3c)", async () => {
    const client = makeClient();
    render(CitationSummaryPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await summarize();
    await waitFor(() =>
      expect(screen.getByTestId("summary-coverage")).toBeTruthy(),
    );
    const coverage = screen.getByTestId("summary-coverage");
    expect(coverage.textContent).toContain("62%");
    expect(coverage.textContent).toContain("312 / 503");
  });

  it("opens an internal item directly in the paper view via the icon button (C2)", async () => {
    const client = makeClient();
    render(CitationSummaryPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await summarize();
    await waitFor(() =>
      expect(screen.getAllByTestId("open-paper-view").length).toBeGreaterThan(
        0,
      ),
    );
    await fireEvent.click(screen.getAllByTestId("open-paper-view")[0]);
    expect(client.getWork).toHaveBeenCalledWith("w1");
  });

  it("fetches and renders an external preview on demand (C1)", async () => {
    const client = makeClient({
      externalPreview: vi.fn().mockResolvedValue({
        available: true,
        title: "Attention Is All You Need",
        authors: ["Vaswani"],
        year: 2017,
        venue: "NeurIPS",
        abstract: "The dominant sequence transduction models...",
        doi: "10.9/missing",
        arxiv_id: "1706.03762",
        sources: ["crossref"],
        message: null,
      }),
    });
    render(CitationSummaryPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await summarize();
    await waitFor(() =>
      expect(screen.getByTestId("summary-preview-toggle")).toBeTruthy(),
    );
    await fireEvent.click(screen.getByTestId("summary-preview-toggle"));
    expect(client.externalPreview).toHaveBeenCalledWith({
      doi: "10.9/missing",
      arxiv: "1706.03762",
      referenceId: "ref-1",
    });
    await waitFor(() =>
      expect(screen.getByText(/dominant sequence transduction/)).toBeTruthy(),
    );
    expect(screen.getByText("NeurIPS", { exact: false })).toBeTruthy();
  });

  it("shows Venue and Author sub-tabs with aggregated stats (batch10 #7)", async () => {
    const client = makeClient({
      venueAuthorSummary: vi.fn().mockResolvedValue({
        scope_work_count: 3,
        venues: [
          { name: "NeurIPS", count: 2, pct: 66.7, year_min: 2019, year_max: 2021, variants: ["NeurIPS", "neurips"] },
          { name: "ICML", count: 1, pct: 33.3, year_min: 2020, year_max: 2020, variants: ["ICML"] },
        ],
        authors: [
          { name: "Vaswani, A.", count: 2, pct: 66.7, variants: ["Vaswani, A.", "Ashish Vaswani"] },
        ],
        papers_without_venue: 0,
        papers_without_authors: 1,
        distinct_venue_count: 2,
        distinct_author_count: 1,
        notes: [],
      }),
    });
    render(CitationSummaryPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await summarize();

    await fireEvent.click(await screen.findByTestId("cs-tab-venues"));
    const venueTable = await screen.findByTestId("venue-table");
    expect(venueTable.textContent).toContain("NeurIPS");
    expect(venueTable.textContent).toContain("2019–2021");
    expect(venueTable.textContent).toContain("ICML");

    await fireEvent.click(screen.getByTestId("cs-tab-authors"));
    const authorTable = await screen.findByTestId("author-table");
    expect(authorTable.textContent).toContain("Vaswani, A.");
    expect(authorTable.textContent).toContain("form(s)"); // merged variants surfaced
  });

  it("shows a graceful message when no preview is available (C1)", async () => {
    const client = makeClient({
      externalPreview: vi
        .fn()
        .mockResolvedValue({
          available: false,
          message: "No preview available.",
          authors: [],
          sources: [],
        }),
    });
    render(CitationSummaryPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await summarize();
    await fireEvent.click(await screen.findByTestId("summary-preview-toggle"));
    await waitFor(() =>
      expect(screen.getByText("No preview available.")).toBeTruthy(),
    );
  });

  it("marks a missing work ignored and moves it to the collapsible (C3a)", async () => {
    const client = makeClient({
      setWorklistDecision: vi
        .fn()
        .mockResolvedValue({ "doi:10.9/missing": "ignore" }),
    });
    render(CitationSummaryPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await summarize();
    await fireEvent.click(await screen.findByTestId("summary-ignore"));
    expect(client.setWorklistDecision).toHaveBeenCalledWith(
      "doi:10.9/missing",
      "ignore",
    );
    await waitFor(() =>
      expect(screen.getByTestId("summary-ignored")).toBeTruthy(),
    );
    expect(screen.getByTestId("summary-ignored").textContent).toContain(
      "Ignored (1)",
    );
  });

  it("restores decisions from the server on load (persist across visits) (C3a)", async () => {
    const client = makeClient({
      getWorklist: vi.fn().mockResolvedValue({ "doi:10.9/missing": "ignore" }),
    });
    render(CitationSummaryPage, { client: client as never });
    await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
    await summarize();
    // The remembered ignore keeps the item out of the active list and inside the collapsible.
    await waitFor(() =>
      expect(screen.getByTestId("summary-ignored")).toBeTruthy(),
    );
    expect(screen.queryByTestId("summary-queue")).toBeNull();
  });

  it("exports the missing list, downloading the server-named file per format (C3b)", async () => {
    const client = makeClient({
      exportMissingWorks: vi
        .fn()
        .mockImplementation(({ format }: { format: string }) =>
          Promise.resolve(
            format === "csv"
              ? {
                  filename: "cited-but-missing.csv",
                  content_type: "text/csv",
                  content: "a,b\n",
                }
              : {
                  filename: "cited-but-missing.bib",
                  content_type: "application/x-bibtex",
                  content: "@misc{}",
                },
          ),
        ),
    });
    // The export builds an <a href=blob download=filename> and clicks it to save the file. jsdom
    // can't perform that navigation, so capture the click and assert the user actually gets the
    // right file (the server-provided name, per format) — a behavioural assertion, not just that
    // the API was called. Capturing the click is also what keeps jsdom from logging
    // "Not implemented: navigation to another Document".
    const downloads: string[] = [];
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        downloads.push(this.download);
      });
    try {
      render(CitationSummaryPage, { client: client as never });
      await waitFor(() => expect(client.listShelves).toHaveBeenCalled());
      await summarize();

      await fireEvent.click(await screen.findByTestId("summary-export-bibtex"));
      expect(client.exportMissingWorks).toHaveBeenCalledWith(
        expect.objectContaining({ format: "bibtex" }),
      );
      await waitFor(() => expect(downloads).toContain("cited-but-missing.bib"));

      await fireEvent.click(screen.getByTestId("summary-export-csv"));
      expect(client.exportMissingWorks).toHaveBeenCalledWith(
        expect.objectContaining({ format: "csv" }),
      );
      await waitFor(() => expect(downloads).toContain("cited-but-missing.csv"));

      // Each export created (and should later revoke) exactly one object URL for its blob.
      expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
      expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
    } finally {
      clickSpy.mockRestore();
    }
  });
});
