# Contributing

PaperRacks is currently scaffolded for multi-agent implementation. Contributions should follow the work packages in `WORK_SPLIT.md`.

## Development expectations

- Keep changes small and testable.
- Update `PROGRESS.md` after completing a milestone or vertical slice.
- Update docs when APIs, config, security behavior, or user-visible behavior changes.
- Add or update tests for new service logic.
- Keep security-sensitive code explicit and easy to review.

## Formatting

Backend and agent Python code should be formatted with Ruff/Black-compatible defaults. Frontend code should use the package-level formatter once the frontend toolchain is finalized.

## Pull request checklist

- [ ] Tests added or consciously deferred with reason.
- [ ] Security implications considered.
- [ ] Config examples updated.
- [ ] Docs updated.
- [ ] `PROGRESS.md` updated.
- [ ] Agent handoff note added under `docs/agent_handoffs/`.
