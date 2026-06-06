# AI Service Desk Autopilot — common tasks.
# `make dev` is the one-command local runner (backend + frontend).

.DEFAULT_GOAL := help
.PHONY: help dev mock install test build up down

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

dev:  ## Run backend (:8000) + frontend (:5173) locally — uses real Phoenix from .env
	./dev.sh

mock:  ## Same as dev, but also start the mock Phoenix ERP (:9000)
	./dev.sh --mock

install:  ## Create the backend venv and install backend + frontend deps
	cd backend && python3.12 -m venv .venv && .venv/bin/python -m pip install -U pip && .venv/bin/python -m pip install -r requirements.txt
	cd frontend && npm install

test:  ## Run the backend test suite (offline; no creds needed)
	cd backend && .venv/bin/python -m pytest -q

build up:  ## Build + run the whole stack in Docker (backend + frontend)
	docker compose up --build

down:  ## Stop the Docker stack
	docker compose down
