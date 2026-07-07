import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Work } from "../api/client";
import {
  pendingLibraryOpen,
  pendingLibrarySearch,
  selectedPaperIds,
  selectedWorkId,
} from "../lib/selection";
import LibraryPage from "./LibraryPage.svelte";

function work(id: string, title: string): Work {
  return {
    id,
    canonical_title: title,
    reading_status: "unread",
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
  } as Work;
}

function page(items: Work[]) {
  return { items, total: items.length, page: 1, pages: 1, per_page: 100 };
}

describe("LibraryPage refresh button (issue batch 6 #7)", () => {
  beforeEach(() => {
    selectedPaperIds.set([]);
    selectedWorkId.set(null);
    pendingLibrarySearch.set(null);
    pendingLibraryOpen.set(null);
  });
  afterEach(() => vi.restoreAllMocks());

  it("re-fetches the works list (and picks up newly-pushed papers) without a page reload", async () => {
    // Second load returns an extra paper, as if the agent pushed one between renders.
    const listWorks = vi
      .fn()
      .mockResolvedValueOnce(page([work("w1", "Paper One")]))
      .mockResolvedValue(
        page([work("w1", "Paper One"), work("w2", "Freshly Pushed")]),
      );
    const client = {
      listShelves: vi.fn().mockResolvedValue([]),
      listRacks: vi.fn().mockResolvedValue([]),
      listTags: vi.fn().mockResolvedValue([]),
      listSavedFilters: vi.fn().mockResolvedValue([]),
      getPreferences: vi.fn().mockResolvedValue({}),
      listWorks,
    };
    render(LibraryPage, { client: client as never });
    await waitFor(() => expect(listWorks).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("Freshly Pushed")).toBeNull();

    await fireEvent.click(screen.getByRole("button", { name: /^refresh$/i }));

    await waitFor(() => expect(listWorks).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByText("Freshly Pushed")).toBeTruthy(),
    );
  });
});
