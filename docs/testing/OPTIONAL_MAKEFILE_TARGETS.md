# Optional Makefile Targets for the Additional Test Battery

No Makefile changes are required. The existing `make test` and `make frontend-test`
will discover the added tests automatically.

If you want focused commands, add this optional block to the Makefile:

```makefile
.PHONY: test-added-backend
test-added-backend: init ## Run additional backend test battery only.
	$(API_RUN_NODEPS) python -m pytest \
		backend/tests/test_additional_security_contracts.py \
		backend/tests/test_additional_library_contracts.py \
		backend/tests/test_additional_algorithm_contracts.py

.PHONY: test-added-agent
test-added-agent: init ## Run additional agent test battery only.
	$(AGENT_RUN) python -m pytest agent/tests/test_additional_agent_security.py

.PHONY: test-added
test-added: test-added-backend test-added-agent frontend-test ## Run additional backend/agent tests plus frontend tests.
```

Do not add the future skipped tests to a special required target until the
corresponding features are implemented and the skip markers are removed.
```
