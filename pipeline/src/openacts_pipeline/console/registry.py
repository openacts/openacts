"""What each pipeline stage needs, so its page can be generated rather than written.

Stages are close in shape without being uniform: four of the seven have a
separate execute, `candidate` takes a second input, and `review` takes a verdict.
The registry records that variation so a new stage costs an entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openacts_pipeline.config import DEFAULT_MODEL_BACKEND, MODEL_BACKENDS

MODEL_CHOICES = (
    DEFAULT_MODEL_BACKEND,
    *sorted(MODEL_BACKENDS - {DEFAULT_MODEL_BACKEND}),
)


@dataclass(frozen=True)
class Param:
    """One extra control on a stage's form, beyond its primary input."""

    name: str
    label: str
    folder: str | None = None
    choices: tuple[str, ...] = ()
    env: str | None = None
    """Set this environment variable for the run instead of passing an argument."""


@dataclass(frozen=True)
class Stage:
    name: str
    summary: str
    input_folder: str
    input_label: str
    extra: tuple[Param, ...] = ()
    executable: bool = True
    long_running: bool = False
    writes_corpus: bool = False
    outputs: str | None = None

    def command(
        self, cache_root: Path, values: dict[str, str], *, execute: bool
    ) -> list[str]:
        argv = [self.name, str(cache_root / self.input_folder / values["input"])]
        for extra in self.extra:
            if extra.env is not None:
                continue
            value = values[extra.name]
            argv.append(
                str(cache_root / extra.folder / value) if extra.folder else value
            )
        argv += ["--cache-root", str(cache_root)]
        if execute and self.executable:
            argv.append("--execute")
        return argv

    def environment(self, values: dict[str, str]) -> dict[str, str]:
        chosen: dict[str, str] = {}
        for extra in self.extra:
            if extra.env is None or not values.get(extra.name):
                continue
            chosen[extra.env] = values[extra.name]
        return chosen


STAGES: tuple[Stage, ...] = (
    Stage(
        "acquire",
        "Download and fingerprint one approved PDF.",
        "requests",
        "request",
        long_running=True,
        outputs="runs",
    ),
    Stage(
        "classify",
        "Measure cached PDF text coverage.",
        "runs",
        "receipt",
        executable=False,
        outputs="classifications",
    ),
    Stage(
        "extract",
        "Extract classified pages with native text and OCR.",
        "classifications",
        "classification",
        executable=False,
        long_running=True,
        outputs="extractions",
    ),
    Stage(
        "structure",
        "Infer a reviewable hierarchy from extracted text.",
        "extractions",
        "extraction",
        extra=(
            Param(
                "backend",
                "Model backend",
                choices=MODEL_CHOICES,
                env="OPENACTS_MODEL_BACKEND",
            ),
        ),
        long_running=True,
        outputs="structures",
    ),
    Stage(
        "candidate",
        "Materialize a corpus-shaped review candidate.",
        "structures",
        "structure",
        extra=(Param("act", "Act record", folder="acts"),),
        executable=False,
        outputs="corpus-candidates",
    ),
    Stage(
        "review",
        "Record a fidelity verdict across a candidate.",
        "corpus-candidates",
        "candidate",
        extra=(Param("fidelity", "fidelity", choices=("single_reviewed",)),),
    ),
    Stage(
        "promote",
        "Write a reviewed candidate to the authored corpus.",
        "corpus-candidates",
        "candidate",
        writes_corpus=True,
    ),
)

BY_NAME: dict[str, Stage] = {stage.name: stage for stage in STAGES}


def artifacts(cache_root: Path, folder: str) -> list[str]:
    directory = cache_root / folder
    if not directory.is_dir():
        return []
    entries = [
        entry
        for entry in directory.iterdir()
        if entry.is_dir() or entry.suffix == ".json"
    ]
    entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    return [entry.name for entry in entries]
