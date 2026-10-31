PYTHON ?= python

.PHONY: run test lint demo setup auth-setup ledger-init ledger-demo provider-demo context-demo api-demo
api-demo:
	$(PYTHON) -m scripts.api_demo
context-demo:
	$(PYTHON) -m scripts.context_demo
provider-demo:
	$(PYTHON) -m scripts.provider_demo
ledger-init:
	$(PYTHON) -m scripts.init_ledger
ledger-demo:
	$(PYTHON) -m scripts.ledger_demo
setup:
	@test -f .env || (cp .env.example .env && chmod 600 .env)
	$(PYTHON) -m scripts.configure_auth --demo
auth-setup:
	$(PYTHON) -m scripts.configure_auth
run:
	$(PYTHON) -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
test:
	$(PYTHON) -m pytest
lint:
	$(PYTHON) -m ruff check .
demo:
	$(PYTHON) -m scripts.demo
