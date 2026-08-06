.PHONY: setup test lint check acquire acquire-execute classify

setup:
	uv sync --project pipeline

test:
	uv run --project pipeline pytest tests/test_contract.py pipeline/tests

lint:
	uv run --project pipeline ruff check pipeline/src pipeline/tests tests/test_contract.py

check: lint test

acquire:
	@test -n "$(REQUEST)" || (echo "REQUEST is required" >&2; exit 2)
	uv run --project pipeline openacts acquire "$(REQUEST)"

acquire-execute:
	@test -n "$(REQUEST)" || (echo "REQUEST is required" >&2; exit 2)
	uv run --project pipeline openacts acquire "$(REQUEST)" --execute

classify:
	@test -n "$(RECEIPT)" || (echo "RECEIPT is required" >&2; exit 2)
	uv run --project pipeline openacts classify "$(RECEIPT)"
