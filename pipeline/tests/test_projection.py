import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openacts_pipeline.common import PipelineError
from openacts_pipeline.projection import build_projection_rows, load_tagged_corpus

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/valid"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _release_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "OpenActs Test")
    _git(repo, "config", "user.email", "test@openacts.local")
    shutil.copytree(ROOT / "schemas", repo / "schemas")

    act = _load("act.json")
    first = _load("provision.json")
    child = copy.deepcopy(first)
    child.update(
        {
            "provision_id": f"{act['act_id']}:section-1.subsection-1",
            "node_type": "subsection",
            "display_label": "(1)",
            "heading": None,
            "parent_provision_id": first["provision_id"],
            "order": 1,
        }
    )
    child["content_blocks"][0]["text"] = "Child wording."
    second = copy.deepcopy(first)
    second.update(
        {
            "provision_id": f"{act['act_id']}:section-2",
            "display_label": "2.",
            "heading": "Table provision",
            "parent_provision_id": None,
            "order": 2,
            "source_spans": _load("table.json")["source_spans"],
            "content_blocks": [_load("table.json")],
        }
    )

    act_dir = repo / "corpus/ng/federal/acts/2023/37"
    _write_json(act_dir / "act.json", act)
    _write_jsonl(repo / "corpus/sources.jsonl", [_load("source.json")])
    _write_jsonl(act_dir / "provisions.jsonl", [first, child, second])
    _write_jsonl(act_dir / "citations.jsonl", [_load("citation.json")])
    _git(repo, "add", "corpus", "schemas")
    _git(repo, "commit", "-q", "-m", "test corpus")
    _git(repo, "tag", "-a", "corpus-v0.0.0", "-m", "test release")
    return repo, act_dir


def test_tagged_projection_uses_exact_commit_and_deterministic_rows(
    tmp_path: Path,
) -> None:
    repo, act_dir = _release_repo(tmp_path)
    expected_commit = _git(repo, "rev-parse", "HEAD")

    changed_act = _load("act.json")
    changed_act["titles"]["official"] = "Uncommitted title"
    _write_json(act_dir / "act.json", changed_act)
    (repo / "schemas/act.schema.json").unlink()

    tagged = load_tagged_corpus(repo, "corpus-v0.0.0")
    rows = build_projection_rows(tagged)

    assert tagged.commit_sha == expected_commit
    assert tagged.records.acts[0]["titles"]["official"] == "Example Act 2023"
    assert rows.canonical_schema_versions == ("0.1.0",)
    assert rows.counts == {
        "acts": 1,
        "sources": 1,
        "provisions": 3,
        "citations": 1,
    }
    assert rows.acts[0].title_keys == (
        "example act 2023",
        "example act",
        "an act used only to validate the openacts corpus contract",
    )
    assert rows.acts[0].citation_key == "act no 37 of 2023"
    assert [row.sequence for row in rows.provisions] == [1, 2, 3]
    assert [row.depth for row in rows.provisions] == [0, 1, 0]
    assert [row.reference_key for row in rows.provisions] == [
        "section 1",
        "section 1 subsection 1",
        "section 2",
    ]
    assert "Child wording." in rows.provisions[1].searchable_text
    assert "Example fees" in rows.provisions[2].searchable_text
    assert "100" in rows.provisions[2].searchable_text

    tagged.records.acts[0]["schema_version"] = "0.2.0"
    with pytest.raises(PipelineError) as unsupported_schema:
        build_projection_rows(tagged)
    assert unsupported_schema.value.code == "unsupported_canonical_schema"


def test_tagged_projection_blocks_bad_tags_and_invalid_tagged_data(
    tmp_path: Path,
) -> None:
    repo, act_dir = _release_repo(tmp_path)

    with pytest.raises(PipelineError) as malformed:
        load_tagged_corpus(repo, "main")
    assert malformed.value.code == "invalid_release_tag"

    with pytest.raises(PipelineError) as missing:
        load_tagged_corpus(repo, "corpus-v0.0.9")
    assert missing.value.code == "release_tag_not_found"

    invalid_act = _load("act.json")
    invalid_act["record_type"] = "source"
    _write_json(act_dir / "act.json", invalid_act)
    _git(repo, "add", "corpus")
    _git(repo, "commit", "-q", "-m", "invalid corpus")
    _git(repo, "tag", "-a", "corpus-v0.0.1", "-m", "invalid release")

    with pytest.raises(PipelineError) as invalid:
        load_tagged_corpus(repo, "corpus-v0.0.1")
    assert invalid.value.code == "invalid_corpus_record"
