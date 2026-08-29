"""The console application.

Binds to loopback only. It holds corpus write access through `promote`, so it is
deliberately a separate application from the public reader in `api/` and is never
deployed with it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from openacts_pipeline.common import PipelineError, local_clock
from openacts_pipeline.console import acts
from openacts_pipeline.console import review as review_model
from openacts_pipeline.console.jobs import Job, JobStore
from openacts_pipeline.console.registry import BY_NAME, STAGES, artifacts
from openacts_pipeline.console.state import (
    STAGE_ORDER,
    label_for,
    survey,
    titles_by_digest,
)
from openacts_pipeline.corpus import REVIEWED_FIDELITIES, review

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
POLL_SECONDS = 0.4

SOURCE_CLASSES = (
    "official_gazette",
    "certified_legislative_copy",
    "official_agency_copy",
    "official_translation",
    "institutional_copy",
    "secondary_copy",
)
FILTERS = ("all", "needs action", "in corpus", "blocked")


def create_app(cache_root: Path, project_root: Path) -> FastAPI:
    app = FastAPI(title="OpenActs console", docs_url=None, redoc_url=None)
    jobs = JobStore(cache_root, project_root)
    corpus_root = project_root / "corpus"

    def render(request: Request, template: str, **context) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request, template, {"cache_root": cache_root, **context}
        )

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, show: str = "all") -> HTMLResponse:
        if show not in FILTERS:
            raise HTTPException(status_code=400, detail=f"unknown filter {show}")
        rows = survey(cache_root, corpus_root)
        if show == "needs action":
            rows = [row for row in rows if row.next_stage and not row.blocked]
        elif show == "in corpus":
            rows = [row for row in rows if row.in_corpus]
        elif show == "blocked":
            rows = [row for row in rows if row.blocked]
        return render(
            request,
            "overview.html",
            sources=rows,
            stages=STAGES,
            stage_order=STAGE_ORDER,
            show=show,
            filters=FILTERS,
            source_classes=SOURCE_CLASSES,
        )

    @app.post("/sources")
    async def add_source(request: Request) -> RedirectResponse:
        """Write an acquisition request and immediately start acquiring it."""
        values = {key: str(value).strip() for key, value in (await request.form()).items()}
        missing = [
            field
            for field in ("url", "document_title", "document_publisher",
                          "provider_name", "source_class")
            if not values.get(field)
        ]
        if missing:
            raise HTTPException(
                status_code=400, detail=f"missing: {', '.join(missing)}"
            )
        if values["source_class"] not in SOURCE_CLASSES:
            raise HTTPException(status_code=400, detail="unknown source_class")
        name = _request_filename(cache_root, values["document_title"])
        path = cache_root / "requests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {field: values[field] for field in
                 ("url", "provider_name", "document_title",
                  "document_publisher", "source_class")},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        stage = BY_NAME["acquire"]
        job = jobs.start(
            "acquire",
            stage.command(cache_root, {"input": name}, execute=True),
            execute=True,
        )
        return RedirectResponse(f"/jobs/{job.job_id}", status_code=303)

    @app.get("/stage/{name}", response_class=HTMLResponse)
    def stage_form(request: Request, name: str) -> HTMLResponse:
        stage = BY_NAME.get(name)
        if stage is None:
            raise HTTPException(status_code=404, detail=f"no stage named {name}")
        titles = titles_by_digest(cache_root)

        def labelled(folder: str) -> list[tuple[str, str]]:
            return [
                (entry, label_for(entry, titles))
                for entry in artifacts(cache_root, folder)
            ]

        return render(
            request,
            "stage.html",
            stage=stage,
            inputs=labelled(stage.input_folder),
            extra_inputs={
                param.name: labelled(param.folder)
                for param in stage.extra
                if param.folder
            },
        )

    @app.post("/stage/{name}")
    async def run_stage(request: Request, name: str) -> RedirectResponse:
        stage = BY_NAME.get(name)
        if stage is None:
            raise HTTPException(status_code=404, detail=f"no stage named {name}")
        form = await request.form()
        values = {key: str(value) for key, value in form.items()}
        execute = values.get("mode") == "execute"
        if execute and not stage.executable:
            raise HTTPException(
                status_code=400, detail=f"{name} has no separate execute mode"
            )
        # Every chosen name becomes part of a filesystem path on a console that
        # can write the corpus, so it has to be one this cache actually offers.
        _require_offered(cache_root, stage.input_folder, values.get("input"))
        for param in stage.extra:
            if param.folder:
                _require_offered(cache_root, param.folder, values.get(param.name))
            elif param.choices and values.get(param.name) not in param.choices or param.choices and values.get(param.name) not in param.choices:
                raise HTTPException(
                    status_code=400,
                    detail=f"{param.name} must be one of {list(param.choices)}",
                )
        job = jobs.start(
            name,
            stage.command(cache_root, values, execute=execute),
            execute=execute,
            env=stage.environment(values),
        )
        return RedirectResponse(f"/jobs/{job.job_id}", status_code=303)

    @app.get("/acts/new", response_class=HTMLResponse)
    def act_form(request: Request, digest: str) -> HTMLResponse:
        receipt = _receipt_for(cache_root, digest)
        if receipt is None:
            raise HTTPException(status_code=404, detail=f"no acquired Source {digest}")
        return render(
            request,
            "act.html",
            draft=acts.draft_from_receipt(receipt),
            text_kinds=acts.TEXT_KINDS,
            statuses=acts.STATUSES,
        )

    @app.post("/acts")
    async def create_act(request: Request) -> RedirectResponse:
        values = {key: str(value) for key, value in (await request.form()).items()}
        receipt = _receipt_for(cache_root, values.get("digest", ""))
        if receipt is None:
            raise HTTPException(status_code=404, detail="no such acquired Source")
        source_id = (receipt.get("source") or {}).get("source_id", "")
        try:
            record = acts.build(values, source_id)
        except PipelineError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        acts.write(cache_root, record)
        return RedirectResponse("/stage/candidate", status_code=303)

    @app.get("/review/{name}", response_class=HTMLResponse)
    def review_page(
        request: Request, name: str, show: str = "attention"
    ) -> HTMLResponse:
        _require_offered(cache_root, "corpus-candidates", name)
        candidate = review_model.load(cache_root, name)
        listed = {
            "attention": candidate.attention,
            "unreviewed": [p for p in candidate.provisions if not p.reviewed],
            "all": candidate.provisions,
        }
        if show not in listed:
            raise HTTPException(status_code=400, detail=f"unknown filter {show}")
        return render(
            request,
            "review.html",
            candidate=candidate,
            listed=listed[show],
            show=show,
            fidelities=REVIEWED_FIDELITIES,
        )

    @app.post("/review/{name}")
    async def record_verdict(request: Request, name: str) -> RedirectResponse:
        _require_offered(cache_root, "corpus-candidates", name)
        values = dict(await request.form())
        provision = str(values.get("provision") or "") or None
        try:
            review(
                cache_root / "corpus-candidates" / name,
                str(values.get("fidelity", "")),
                provision_id=provision,
                execute=True,
            )
        except PipelineError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        show = str(values.get("show", "attention"))
        return RedirectResponse(f"/review/{name}?show={show}", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def job_list(request: Request) -> HTMLResponse:
        return render(request, "jobs.html", jobs=jobs.listing())

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str) -> HTMLResponse:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="unknown job")
        job = jobs[job_id]
        return render(
            request,
            "job.html",
            job=job,
            command=" ".join(job.argv),
            events=[_summarise(event) for event in job.progress(limit=200)],
            result=job.result(),
        )

    @app.post("/jobs/{job_id}/stop")
    def stop_job(job_id: str) -> RedirectResponse:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="unknown job")
        jobs[job_id].stop()
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/jobs/{job_id}/events")
    async def job_events(job_id: str) -> StreamingResponse:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="unknown job")
        return StreamingResponse(
            _stream(jobs[job_id]), media_type="text/event-stream"
        )

    return app


def _receipt_for(cache_root: Path, digest: str) -> dict | None:
    """The successful acquisition receipt for one Source digest.

    A receipt is named for the request that produced it, not for the Source it
    fetched, so the digest has to be read out of the payload.
    """
    runs = cache_root / "runs"
    if not digest or not runs.is_dir():
        return None
    for path in sorted(runs.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("status") != "success":
            continue
        source_id = (payload.get("source") or {}).get("source_id", "")
        if str(source_id).removeprefix("sha256:")[:8] == digest:
            return payload
    return None


def _request_filename(cache_root: Path, title: str) -> str:
    """A readable filename for a new request, without overwriting an existing one."""
    slug = "".join(
        character if character.isalnum() else "-" for character in title.lower()
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug[:60] or "request"
    candidate = f"{slug}.json"
    index = 2
    while (cache_root / "requests" / candidate).exists():
        candidate = f"{slug}-{index}.json"
        index += 1
    return candidate


def _require_offered(cache_root: Path, folder: str, chosen: str | None) -> None:
    if not chosen or chosen not in artifacts(cache_root, folder):
        raise HTTPException(
            status_code=400, detail=f"{chosen or '(nothing)'} is not in {folder}/"
        )


def _summarise(event: dict) -> str:
    """One progress line: the moment, the event, and the keys that change."""
    stamp = local_clock(str(event.get("timestamp", "")))
    name = event.get("event") or event.get("status") or "event"
    detail = " ".join(
        f"{key}={value}"
        for key, value in event.items()
        if key
        not in {"type", "timestamp", "event", "status", "usage", "source_id", "error"}
        and not isinstance(value, dict | list)
    )
    # A failure carries what went wrong in a nested object; dropping it because
    # it is not a scalar leaves a line that says only that something failed.
    error = event.get("error")
    if isinstance(error, dict):
        detail = f"{error.get('code', 'error')}: {error.get('message', '')}".strip()
    return f"{stamp}  {name:<28} {detail}".rstrip()


async def _stream(job: Job) -> AsyncIterator[bytes]:
    """Follow the progress file until the stage exits, then say so."""
    sent = 0
    while True:
        # Read exit state first: anything the stage wrote before exiting is then
        # certain to be in the read that follows.
        finished = not job.running
        events = job.progress()
        for event in events[sent:]:
            yield f"event: progress\ndata: {_summarise(event)}\n\n".encode()
        sent = len(events)
        if finished:
            yield b"event: done\ndata: exited\n\n"
            return
        await asyncio.sleep(POLL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(prog="openacts-console")
    parser.add_argument("--cache-root", type=Path, default=Path("source-cache"))
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args(argv)

    project_root = Path.cwd()
    app = create_app(args.cache_root.resolve(), project_root)
    print(json.dumps({"console": f"http://127.0.0.1:{args.port}"}))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
