# Development Setup Runbook

1. Copy example config files.
2. Start infrastructure with `make dev-up`.
3. Start backend with `make backend-dev`.
4. Start frontend with `cd frontend && npm install && npm run dev`.
5. Run the local agent with `cd agent && python -m paperracks_agent.cli scan ~/papers`.

Do not enable LAN binding until authentication is implemented and verified.
