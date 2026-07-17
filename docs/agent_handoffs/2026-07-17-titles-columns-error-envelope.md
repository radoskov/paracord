# Handoff: ALL-CAPS title taming, column width ratios + dividers, error envelope

## Files changed

- `frontend/src/lib/titleCase.ts` (+ tests) — `tameTitle()`: >10 chars & >95% caps → title case;
  small-words list lowercase (capitalized at segment starts, incl. after `:`); digit tokens and
  strict-shape Roman numerals keep case; ≤5-letter words absent from an embedded common-word
  list stay caps (acronym heuristic — no external dictionary dependency). Common-word reading
  wins over the Roman one (MIX). Used in `PaperTable` title cells and the `WorkDetail` heading;
  the edit form intentionally shows the raw stored title.
- `frontend/src/lib/columns.ts` (+ tests) — `ColumnDef.width` default ratios; `ColumnPrefs`
  gains `widths` (always fully populated after normalize; clamped 2–80) and `dividers`;
  `columnWidthPercents()` implements the ratio mechanic (share of the sum over the SHOWN set).
- `frontend/src/components/ColumnPicker.svelte` — width number input between name and order
  arrows; "Divider lines between rows" checkbox; Apply emits `{order, visible, widths, dividers}`.
- `frontend/src/components/PaperTable.svelte` — `<colgroup>` percentages (fixed layout),
  `no-dividers` class (drops td borders, keeps the header underline), th ellipsis truncation,
  `td select { max-width: 100% }`, `overflow-wrap: anywhere` on cells.
- `frontend/src/pages/LibraryPage.svelte` — passes widths/dividers through picker + table;
  `applyColumns` persists them via the existing localStorage + debounced backend flow.
- `backend/app/api/v1/endpoints/preferences.py` — explicit `widths`/`dividers` fields (the blob
  was already forward-compatible via `extra="allow"`); `backend/openapi.json` regenerated.
- `backend/app/main.py` — `_error_envelope` middleware: X-Request-ID on every response,
  `DataError` → 400 with the DB reason, other unhandled exceptions → 500 naming class+message,
  ids embedded in details and in the logged traceback. Registered BEFORE `add_middleware(CORS)`
  so CORS wraps it — an `Exception` handler would run in Starlette's outermost
  ServerErrorMiddleware and bypass CORS (the "NetworkError" hiding mode). CORS now exposes
  `X-Request-ID`.
- `backend/tests/test_error_envelope.py` — 5 tests (envelope, DataError mapping, id echo/honor,
  HTTPException passthrough).

## Assumptions made

- Title taming is display-only and always-on (no per-user toggle); the >95% trigger keeps it
  away from normal titles. Known trade-offs documented in the module docstring (uncommon long
  acronyms get cased; rare short words stay caps).
- Divider toggle interprets the "thin grey lines" as the row borders (the only dividers the
  table has); the header underline stays as the sort-row anchor.
- Error envelope exposes exception class + message to clients BY DESIGN (self-hosted LAN tool,
  diagnosability over secrecy — owner request). Revisit if the deployment model ever changes.

## Tests added or skipped

- +11 titleCase, +5 columns, +5 envelope. Full battery green: backend 1245, frontend 321,
  safety 161, `E2E_ONLINE=1` e2e 37/37, 0 flaky. Live-verified: width/divider apply + persist
  across reload, X-Request-ID on live responses; e2e_admin's prefs reset to defaults after
  testing.

## Security implications

- Envelope: 500 bodies now include exception class + first message line (see assumption above);
  no stack traces are ever sent. Request ids are random per request and carry no secrets.
- Width/divider prefs are validated client-side (normalize clamps) and are cosmetic server-side.

## Next recommended task

- Surfacing the request id in the UI's error toasts as a copyable chip (today it is embedded in
  the detail text, which is already enough to grep the logs).
