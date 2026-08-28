"""Stage runs, executed as the ordinary command line in a subprocess.

Running the CLI rather than calling the stage functions keeps the console from
drifting from the commands, and means dry-run and execute behave here exactly as
they do in a terminal. Each job owns a directory holding the command, the result
on stdout, and the progress the stage writes to stderr one JSON object per line.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESULT_FILE = "result.json"
PROGRESS_FILE = "progress.jsonl"
COMMAND_FILE = "command.json"


@dataclass
class Job:
    job_id: str
    stage: str
    argv: list[str]
    directory: Path
    execute: bool
    process: subprocess.Popen[bytes] | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def returncode(self) -> int | None:
        return None if self.process is None else self.process.poll()

    @property
    def status(self) -> str:
        if self.process is None:
            return "abandoned"
        code = self.process.poll()
        if code is None:
            return "running"
        return "finished" if code == 0 else f"failed ({code})"

    def result(self) -> str:
        path = self.directory / RESULT_FILE
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def progress(self, limit: int | None = None) -> list[dict[str, Any]]:
        path = self.directory / PROGRESS_FILE
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
        return events[-limit:] if limit else events

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()


class JobStore:
    """Jobs started by this console, newest first."""

    def __init__(self, cache_root: Path, project_root: Path) -> None:
        self.cache_root = cache_root
        self.project_root = project_root
        self._jobs: dict[str, Job] = {}

    def __contains__(self, job_id: str) -> bool:
        return job_id in self._jobs

    def __getitem__(self, job_id: str) -> Job:
        return self._jobs[job_id]

    def listing(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.job_id, reverse=True)

    def start(
        self,
        stage: str,
        argv: list[str],
        *,
        execute: bool,
        env: dict[str, str] | None = None,
    ) -> Job:
        job_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
        directory = self.cache_root / "console-jobs" / job_id
        directory.mkdir(parents=True, exist_ok=True)
        command = [
            "uv",
            "run",
            "--project",
            "pipeline",
            "openacts",
            *argv,
        ]
        (directory / COMMAND_FILE).write_text(
            json.dumps({"stage": stage, "command": command, "execute": execute}),
            encoding="utf-8",
        )
        with (
            (directory / RESULT_FILE).open("wb") as out,
            (directory / PROGRESS_FILE).open("wb") as err,
        ):
            process = subprocess.Popen(
                command,
                cwd=self.project_root,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                env={**os.environ, **(env or {})},
            )
        job = Job(job_id, stage, argv, directory, execute, process)
        self._jobs[job_id] = job
        return job
