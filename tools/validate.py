"""Run every pipeline stage over a manifest of Acts and report structure fidelity.

The harness is resumable: a stage is skipped when an artifact for the same source
digest already exists in the cache, so a rerun only pays for new work.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from openacts_pipeline.acquire import acquire
from openacts_pipeline.classify import classify
from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import StructureSettings
from openacts_pipeline.extract import extract
from openacts_pipeline.structure import structure


def _digest(source_id: str) -> str:
    """Artifact filenames carry the first eight hex characters of the digest."""
    return source_id.removeprefix("sha256:")[:8]


def _existing(cache_root: Path, folder: str, digest: str) -> Path | None:
    candidates = sorted((cache_root / folder).glob(f"*-{digest}-*.json"))
    return candidates[-1] if candidates else None


def _receipt_for(cache_root: Path, url: str) -> Path | None:
    for path in sorted((cache_root / "runs").glob("*.json"), reverse=True):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "success":
            continue
        http = payload.get("http", {})
        if url in (http.get("requested_url"), http.get("final_url")):
            return path
    return None


def _stage_paths(cache_root: Path, result: dict[str, Any]) -> Path:
    return cache_root / result["result_path"]


def run_act(
    request_path: Path, cache_root: Path, *, execute: bool, stop_after: str
) -> dict[str, Any]:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    record: dict[str, Any] = {"request": request_path.name, "title": request.get(
        "document_title"
    )}

    receipt = _receipt_for(cache_root, request["url"])
    if receipt is None:
        if not execute:
            record["status"] = "needs_download"
            return record
        acquired = acquire(request_path, execute=True, cache_root=cache_root)
        if acquired.get("status") != "success":
            record.update(status="acquire_failed", error=acquired.get("error"))
            return record
        receipt = _stage_paths(cache_root, acquired)

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    source_id = payload["source"]["source_id"]
    digest = _digest(source_id)
    record["source_id"] = source_id

    classification = _existing(cache_root, "classifications", digest)
    if classification is None:
        if not execute:
            record["status"] = "needs_classify"
            return record
        try:
            classification = _stage_paths(
                cache_root, classify(receipt, cache_root=cache_root)
            )
        except PipelineError as exc:
            record.update(status="classify_failed", error=exc.as_dict())
            return record
    report = json.loads(classification.read_text(encoding="utf-8"))
    record["pages"] = report["page_count"]
    record["document_route"] = report["summary"]["proposed_route"]
    record["text_layer"] = report["summary"]["proposed_text_layer"]

    extraction = _existing(cache_root, "extractions", digest)
    if extraction is None:
        if not execute:
            record["status"] = "needs_extract"
            return record
        try:
            extraction = _stage_paths(
                cache_root, extract(classification, cache_root=cache_root)
            )
        except PipelineError as exc:
            record.update(status="extract_failed", error=exc.as_dict())
            return record
    record["extraction"] = extraction.name

    if not execute or stop_after == "extract":
        record["status"] = "ready_for_structure"
        return record

    settings = StructureSettings.from_env()
    record["backend"] = settings.backend
    record["model"] = settings.primary_model
    try:
        result = structure(
            extraction, execute=True, cache_root=cache_root, settings=settings
        )
    except PipelineError as exc:
        record.update(status="structure_failed", error=exc.as_dict())
        return record
    except Exception as exc:  # noqa: BLE001 - one Act must not end the batch
        record.update(status="structure_crashed", error={"detail": str(exc)[:600]})
        return record

    plan = result.get("plan", {})
    audit = result.get("audit", {})
    record.update(
        status=result.get("status", "unknown"),
        legal_range=[
            plan.get("legal_start_pdf_page"),
            plan.get("legal_end_pdf_page"),
        ],
        terminator=bool(plan.get("legal_end_terminator")),
        units=len(plan.get("units", [])),
        provisions=result.get("summary", {}).get("provisions"),
        audit_passed=audit.get("passed"),
        claimed_characters=audit.get("claimed_characters"),
        source_characters=audit.get("source_characters"),
        claimed_markers=audit.get("claimed_markers"),
        source_markers=audit.get("source_markers"),
        usage=result.get("model_run", {}).get("usage"),
    )
    return record


def _table(records: list[dict[str, Any]]) -> str:
    header = (
        f"{'act':<32} {'pages':>5} {'layer':<13} {'status':<16} {'audit':<6} "
        f"{'claimed/source':>16} {'markers':>12}"
    )
    lines = [header, "-" * len(header)]
    for record in records:
        claimed = record.get("claimed_characters")
        source = record.get("source_characters")
        chars = f"{claimed}/{source}" if claimed is not None else "-"
        markers = (
            f"{record.get('claimed_markers')}/{record.get('source_markers')}"
            if record.get("source_markers") is not None
            else "-"
        )
        audit = record.get("audit_passed")
        lines.append(
            f"{(record.get('title') or record['request'])[:32]:<32} "
            f"{record.get('pages', '-'):>5} {record.get('text_layer', '-'):<13} "
            f"{record.get('status', '-'):<16} "
            f"{'pass' if audit else 'fail' if audit is not None else '-':<6} "
            f"{chars:>16} {markers:>12}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openacts-validate")
    parser.add_argument("manifest", type=Path, help="JSON list of request file paths")
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="download sources and run model passes",
    )
    parser.add_argument(
        "--stop-after",
        choices=("extract", "structure"),
        default="structure",
        help="stop before the model passes to acquire and extract only",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    requests = [
        (args.manifest.parent / entry).resolve()
        for entry in json.loads(args.manifest.read_text(encoding="utf-8"))
    ]
    records = [
        run_act(
            path, args.cache_root, execute=args.execute, stop_after=args.stop_after
        )
        for path in requests
    ]
    print(_table(records))
    if args.report:
        args.report.write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"\nreport: {args.report}")
    acceptable = {"success", "ready_for_structure"}
    if not args.execute:
        acceptable |= {"needs_download", "needs_classify", "needs_extract"}
    return 0 if all(record.get("status") in acceptable for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
