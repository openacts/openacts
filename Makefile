.PHONY: setup ocr-setup ocr-setup-execute test lint check acquire acquire-execute classify extract structure structure-execute candidate review review-execute promote promote-execute projection projection-execute

setup:
	uv sync --project pipeline

ocr-setup:
	uv run --project pipeline openacts ocr-setup

ocr-setup-execute:
	uv run --project pipeline --group ocr openacts ocr-setup --execute

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
	uv run --env-file pipeline/.env --project pipeline openacts-projection "$(RELEASE)"

projection-execute:
	@test -n "$(RELEASE)" || (echo "RELEASE is required" >&2; exit 2)
	uv run --env-file pipeline/.env --project pipeline openacts-projection "$(RELEASE)" --execute $(if $(filter 1,$(ALLOW_BOOTSTRAP)),--allow-bootstrap)
