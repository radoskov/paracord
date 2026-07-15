# Workplan — 2026-07-16: AI/Insights follow-ups + download automation

## Done this session (see PROGRESS.md for commits)

- Find-on-web: policy refusals now carry an actionable hint (which policy allows the host / add it
  in Admin → Find-on-web) and the failure message lists the tried URLs, rendered as clickable
  links in the paper view.
- Per-paper short + detailed summaries; scope-summary source dropdown; prettier formatted output;
  summary job progress + cancel — see the individual sections / PROGRESS.

## Springer download: root cause (RESOLVED — configuration, not a bug)

The download machinery works: probed from the live API container, the Springer article resolves,
the PDF link is discovered, and the 1.4 MB PDF streams — but ONLY under the `careful`/`unrestricted`
policy. The live policy was **`restricted`** (built-in open-access hosts only), which refuses
`link.springer.com`. The refusal is now surfaced with an actionable hint. **Action for the owner:**
Admin → Find-on-web → set policy to `careful` (allows known publishers incl. Springer, IEEE, ACM,
Wiley…), or add `link.springer.com` to the allowed list. No headless browser is needed for Springer.

## Discussion: headless browser for JS-only publishers (NOT implemented — decision needed)

A minority of publishers (ScienceDirect is the notable one) build the download control entirely in
JavaScript, so the static HTML has no link to extract — those still end in `manual_upload_needed`.
Options, cheapest first:

1. **Elsevier API (already shipped)** covers ScienceDirect properly for `10.1016` DOIs with a key.
   Most JS-only cases the owner will hit ARE Elsevier, so this may already close the gap in
   practice — worth confirming before adding a browser.
2. **Playwright headless sidecar** (the owner's suggestion). Feasibility notes:
   - We already have Playwright in the `e2e/` dev tooling, but that's a Node dev-dependency, NOT a
     runtime service — the backend is Python and the workers have no browser. A runtime path needs
     either `playwright` (Python) + a Chromium install in the worker image (~400-700 MB) or a
     separate sidecar service (like GROBID/Ollama), reached over HTTP. It would be an **optional
     compose profile**, off by default.
   - Memory: Chromium is ~200-300 MB resident per active page; it does NOT "spool up cheaply" —
     launch latency is ~300-800 ms and each navigation holds that RAM. Acceptable for a
     user-initiated, one-at-a-time download; not for bulk.
   - Security: this is the big one. A headless browser executes arbitrary publisher JS and can be
     steered to arbitrary hosts. The current denylist/SSRF/policy gates are enforced in our httpx
     fetch path — a browser bypasses them unless we (a) route all its navigations through a
     request interceptor that re-applies `_classify_download_host` on every request, and (b) still
     download the final PDF bytes through our gated `_stream_pdf`, using the browser ONLY to
     discover the URL / establish a session. That interceptor is the bulk of the work.
   - Entitlement: a headless browser on the server carries the server's IP, same as our httpx
     fetch — it does NOT gain the user's personal session, so for pure paywalls it's no more
     capable than what we have. Its only advantage is executing the JS that reveals an
     already-entitled (IP-based) download URL.
   - Scope: gate it behind the existing manual triggers only (never the auto/background path), an
     admin toggle, and the same per-user allowance pattern as the Elsevier key.

   **Recommended:** defer until we see real non-Elsevier JS-only failures in practice. If added,
   do it as: browser discovers/《resolves》the PDF URL + cookies → hand that URL (+ cookies) to the
   existing gated `_stream_pdf` for the actual download, so all security gates still apply to the
   bytes we store.

## Open decision points for the owner

1. Set the download policy to `careful` now? (Fixes Springer/IEEE/ACM/Wiley immediately.)
2. Headless browser: build the Playwright sidecar, or rely on the Elsevier API + landing-page
   discovery and revisit only if specific publishers keep failing?
