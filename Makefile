PYTHON ?= python

.PHONY: run test lint demo auth-setup
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
