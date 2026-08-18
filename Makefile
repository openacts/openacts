.PHONY: setup dev db-up db-down ocr-setup ocr-setup-execute test lint check api-check api-run web-check web-run integration-test acquire acquire-execute classify extract structure structure-execute candidate review review-execute promote promote-execute projection projection-execute

ifneq (,$(wildcard .env))
include .env
endif

OPENACTS_PROJECTION_DATABASE_URL ?= postgresql://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@127.0.0.1:$(OPENACTS_POSTGRES_PORT)/$(POSTGRES_DB)
OPENACTS_API_DATABASE_URL ?= postgresql://$(OPENACTS_API_USER):$(OPENACTS_API_PASSWORD)@127.0.0.1:$(OPENACTS_POSTGRES_PORT)/$(POSTGRES_DB)
OPENACTS_TEST_DATABASE_URL ?= postgresql://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@127.0.0.1:$(OPENACTS_POSTGRES_PORT)/$(OPENACTS_TEST_DATABASE)
OPENACTS_API_TEST_DATABASE_URL ?= postgresql://$(OPENACTS_API_USER):$(OPENACTS_API_PASSWORD)@127.0.0.1:$(OPENACTS_POSTGRES_PORT)/$(OPENACTS_TEST_DATABASE)
NEXT_PUBLIC_OPENACTS_API_URL ?= http://127.0.0.1:8000
COMPOSE = docker compose --project-name openacts --file compose.yaml

.env:
	@echo ".env is required; copy .env.example" >&2
	@exit 2

setup:
	uv sync --project pipeline

dev: db-up
	@$(MAKE) --no-print-directory -j2 api-run web-run

db-up: .env
	$(COMPOSE) up -d --wait postgres

db-down:
	$(COMPOSE) down

ocr-setup:
	uv run --project pipeline openacts ocr-setup

ocr-setup-execute:
	uv run --project pipeline --group ocr openacts ocr-setup --execute

test:
	uv run --project pipeline pytest tests/test_contract.py pipeline/tests

lint:
	uv run --project pipeline ruff check pipeline/src pipeline/tests tests/test_contract.py

check: lint test api-check web-check

api-check:
	uv run --project api ruff check api/src api/tests
	uv run --project api pytest api/tests

api-run: .env
	OPENACTS_API_DATABASE_URL="$(OPENACTS_API_DATABASE_URL)" OPENACTS_APPLICATION_REVISION="$$(git rev-parse HEAD)" uv run --env-file .env --project api uvicorn openacts_api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log

web-check:
	NEXT_PUBLIC_OPENACTS_API_URL="$(NEXT_PUBLIC_OPENACTS_API_URL)" OPENACTS_WEB_REVISION="$$(git rev-parse HEAD)" npm --prefix web run check

web-run:
	NEXT_PUBLIC_OPENACTS_API_URL="$(NEXT_PUBLIC_OPENACTS_API_URL)" OPENACTS_WEB_REVISION="$$(git rev-parse HEAD)" npm --prefix web run dev

integration-test: db-up
	OPENACTS_TEST_DATABASE_URL="$(OPENACTS_TEST_DATABASE_URL)" uv run --project pipeline pytest pipeline/tests/test_projection.py
	OPENACTS_PROJECTION_DATABASE_URL="$(OPENACTS_TEST_DATABASE_URL)" uv run --env-file .env --project pipeline openacts-projection corpus-v0.0.0 --execute --allow-bootstrap
	OPENACTS_API_TEST_DATABASE_URL="$(OPENACTS_API_TEST_DATABASE_URL)" uv run --env-file .env --project api pytest api/tests/test_database.py

acquire:
	@test -n "$(REQUEST)" || (echo "REQUEST is required" >&2; exit 2)
	uv run --project pipeline openacts acquire "$(REQUEST)"

acquire-execute:
	@test -n "$(REQUEST)" || (echo "REQUEST is required" >&2; exit 2)
	uv run --project pipeline openacts acquire "$(REQUEST)" --execute

classify:
	@test -n "$(RECEIPT)" || (echo "RECEIPT is required" >&2; exit 2)
	uv run --project pipeline openacts classify "$(RECEIPT)"

extract:
	@test -n "$(CLASSIFICATION)" || (echo "CLASSIFICATION is required" >&2; exit 2)
	uv run --project pipeline openacts extract "$(CLASSIFICATION)"

structure:
	@test -n "$(EXTRACTION)" || (echo "EXTRACTION is required" >&2; exit 2)
	uv run --project pipeline openacts structure "$(EXTRACTION)"

structure-execute:
	@test -n "$(EXTRACTION)" || (echo "EXTRACTION is required" >&2; exit 2)
	uv run --env-file pipeline/.env --project pipeline openacts structure "$(EXTRACTION)" --execute

candidate:
	@test -n "$(STRUCTURE)" || (echo "STRUCTURE is required" >&2; exit 2)
	@test -n "$(ACT)" || (echo "ACT is required" >&2; exit 2)
	uv run --project pipeline openacts candidate "$(STRUCTURE)" "$(ACT)"

review:
	@test -n "$(CANDIDATE)" || (echo "CANDIDATE is required" >&2; exit 2)
	@test -n "$(FIDELITY)" || (echo "FIDELITY is required" >&2; exit 2)
	uv run --project pipeline openacts review "$(CANDIDATE)" "$(FIDELITY)"

review-execute:
	@test -n "$(CANDIDATE)" || (echo "CANDIDATE is required" >&2; exit 2)
	@test -n "$(FIDELITY)" || (echo "FIDELITY is required" >&2; exit 2)
	uv run --project pipeline openacts review "$(CANDIDATE)" "$(FIDELITY)" --execute

promote:
	@test -n "$(CANDIDATE)" || (echo "CANDIDATE is required" >&2; exit 2)
	uv run --project pipeline openacts promote "$(CANDIDATE)"

promote-execute:
	@test -n "$(CANDIDATE)" || (echo "CANDIDATE is required" >&2; exit 2)
	uv run --project pipeline openacts promote "$(CANDIDATE)" --execute

projection:
	@test -n "$(RELEASE)" || (echo "RELEASE is required" >&2; exit 2)
	OPENACTS_PROJECTION_DATABASE_URL="$(OPENACTS_PROJECTION_DATABASE_URL)" uv run --env-file .env --project pipeline openacts-projection "$(RELEASE)"

projection-execute:
	@test -n "$(RELEASE)" || (echo "RELEASE is required" >&2; exit 2)
	OPENACTS_PROJECTION_DATABASE_URL="$(OPENACTS_PROJECTION_DATABASE_URL)" uv run --env-file .env --project pipeline openacts-projection "$(RELEASE)" --execute $(if $(filter 1,$(ALLOW_BOOTSTRAP)),--allow-bootstrap)
