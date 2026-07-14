import { describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../api/client";
import {
  emptyScopeSelection,
  resolveScopeRequest,
  scopeSelectionReady,
  type ScopeSelection,
} from "./scope";

function sel(overrides: Partial<ScopeSelection> = {}): ScopeSelection {
  return { ...emptyScopeSelection(), ...overrides };
}

describe("scopeSelectionReady", () => {
  it("mirrors each scope type's requirement", () => {
    expect(scopeSelectionReady(sel(), 0)).toBe(true); // library needs nothing
    expect(scopeSelectionReady(sel({ scopeType: "shelf" }), 0)).toBe(false);
    expect(scopeSelectionReady(sel({ scopeType: "shelf", scopeId: "s1" }), 0)).toBe(true);
    expect(scopeSelectionReady(sel({ scopeType: "search_result", searchQuery: "  " }), 0)).toBe(false);
    expect(scopeSelectionReady(sel({ scopeType: "search_result", searchQuery: "q" }), 0)).toBe(true);
    expect(scopeSelectionReady(sel({ scopeType: "selected_papers" }), 0)).toBe(false);
    expect(scopeSelectionReady(sel({ scopeType: "selected_papers" }), 2)).toBe(true);
    expect(scopeSelectionReady(sel({ scopeType: "import_batch", batchId: "b1" }), 0)).toBe(true);
    expect(scopeSelectionReady(sel({ scopeType: "saved_filter", savedFilterId: "f1" }), 0)).toBe(true);
  });
});

describe("resolveScopeRequest", () => {
  it("runs the search for a search_result scope and returns the ids", async () => {
    const client = {
      listWorks: vi.fn().mockResolvedValue({ items: [{ id: "w1" }, { id: "w2" }] }),
    } as unknown as ApiClient;
    const req = await resolveScopeRequest(
      client,
      sel({ scopeType: "search_result", searchQuery: "attention" }),
      [],
    );
    expect(client.listWorks).toHaveBeenCalledWith({ q: "attention", perPage: 500 });
    expect(req).toEqual({ scopeType: "search_result", workIds: ["w1", "w2"] });
  });

  it("maps each id-carrying scope to the request shape without touching the API", async () => {
    const client = { listWorks: vi.fn() } as unknown as ApiClient;
    expect(await resolveScopeRequest(client, sel(), [])).toEqual({
      scopeType: "library",
      scopeId: null,
    });
    expect(
      await resolveScopeRequest(client, sel({ scopeType: "rack", scopeId: "r1" }), []),
    ).toEqual({ scopeType: "rack", scopeId: "r1" });
    expect(
      await resolveScopeRequest(client, sel({ scopeType: "selected_papers" }), ["a", "b"]),
    ).toEqual({ scopeType: "selected_papers", workIds: ["a", "b"] });
    expect(
      await resolveScopeRequest(client, sel({ scopeType: "import_batch", batchId: "b1" }), []),
    ).toEqual({ scopeType: "import_batch", scopeId: "b1" });
    expect(
      await resolveScopeRequest(client, sel({ scopeType: "saved_filter", savedFilterId: "f1" }), []),
    ).toEqual({ scopeType: "saved_filter", scopeId: "f1" });
    expect(client.listWorks).not.toHaveBeenCalled();
  });
});
