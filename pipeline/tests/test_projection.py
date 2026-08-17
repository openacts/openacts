import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest

import openacts_pipeline.projection as projection_module
from openacts_pipeline.common import PipelineError
from openacts_pipeline.config import ProjectionSettings
from openacts_pipeline.projection import (
    DatabaseProjectionState,
    DatabaseRelease,
    ProjectionBlocker,
    build_projection_plan,
    build_projection_rows,
    load_tagged_corpus,
    preview_projection,
)

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


def test_projection_plan_reports_import_activate_and_noop(tmp_path: Path) -> None:
    repo, _act_dir = _release_repo(tmp_path)
    rows = build_projection_rows(load_tagged_corpus(repo, "corpus-v0.0.0"))
    empty = DatabaseProjectionState(
        server_major_version=17,
        transaction_read_only=True,
        active_release=None,
        previous_release=None,
        target_release=None,
    )

    import_plan = build_projection_plan(rows, empty)
    assert import_plan["status"] == "ready"
    assert import_plan["action"] == "import_and_activate"
    assert import_plan["writes_performed"] is False
    assert import_plan["transaction_read_only"] is True
    assert import_plan["release"]["bootstrap_release"] is True
    assert import_plan["warnings"][0]["code"] == "bootstrap_release"

    ready = DatabaseRelease(
        release_tag=rows.release_tag,
        commit_sha=rows.commit_sha,
        canonical_schema_versions=rows.canonical_schema_versions,
        projection_schema_version=rows.projection_schema_version,
        import_state="ready",
    )
    inactive = DatabaseProjectionState(
        server_major_version=17,
        transaction_read_only=True,
        active_release=None,
        previous_release=None,
        target_release=ready,
    )
    assert build_projection_plan(rows, inactive)["action"] == "activate_existing"

    active = DatabaseProjectionState(
        server_major_version=17,
        transaction_read_only=True,
        active_release=ready,
        previous_release=None,
        target_release=ready,
    )
    assert build_projection_plan(rows, active)["action"] == "noop"


@pytest.mark.parametrize(
    ("changed_field", "changed_value", "blocker_code"),
    [
        ("commit_sha", "f" * 40, "release_commit_conflict"),
        (
            "canonical_schema_versions",
            ("0.2.0",),
            "release_metadata_conflict",
        ),
        ("projection_schema_version", 2, "release_metadata_conflict"),
        ("import_state", "importing", "release_import_incomplete"),
    ],
)
def test_projection_plan_blocks_conflicting_or_incomplete_releases(
    tmp_path: Path,
    changed_field: str,
    changed_value: object,
    blocker_code: str,
) -> None:
    repo, _act_dir = _release_repo(tmp_path)
    rows = build_projection_rows(load_tagged_corpus(repo, "corpus-v0.0.0"))
    values = {
        "release_tag": rows.release_tag,
        "commit_sha": rows.commit_sha,
        "canonical_schema_versions": rows.canonical_schema_versions,
        "projection_schema_version": rows.projection_schema_version,
        "import_state": "ready",
    }
    values[changed_field] = changed_value
    target = DatabaseRelease(**values)
    state = DatabaseProjectionState(
        server_major_version=17,
        transaction_read_only=True,
        active_release=None,
        previous_release=None,
        target_release=target,
    )

    result = build_projection_plan(rows, state)

    assert result["status"] == "blocked"
    assert result["action"] == "blocked"
    assert [blocker["code"] for blocker in result["blockers"]] == [blocker_code]


def test_projection_plan_preserves_database_blockers(tmp_path: Path) -> None:
    repo, _act_dir = _release_repo(tmp_path)
    rows = build_projection_rows(load_tagged_corpus(repo, "corpus-v0.0.0"))
    state = DatabaseProjectionState(
        server_major_version=17,
        transaction_read_only=True,
        active_release=None,
        previous_release=None,
        target_release=None,
        blockers=(
            ProjectionBlocker(
                code="projection_schema_unavailable",
                message="required projection tables or columns are missing",
            ),
        ),
    )

    result = build_projection_plan(rows, state)

    assert result["status"] == "blocked"
    assert result["action"] == "blocked"
    assert result["blockers"] == [
        {
            "code": "projection_schema_unavailable",
            "message": "required projection tables or columns are missing",
        }
    ]


def test_projection_plan_blocks_unsafe_database_state(tmp_path: Path) -> None:
    repo, _act_dir = _release_repo(tmp_path)
    rows = build_projection_rows(load_tagged_corpus(repo, "corpus-v0.0.0"))
    incomplete = DatabaseRelease(
        release_tag="corpus-v0.0.9",
        commit_sha="e" * 40,
        canonical_schema_versions=("0.1.0",),
        projection_schema_version=1,
        import_state="importing",
    )
    cases = (
        (
            DatabaseProjectionState(16, True, None, None, None),
            "unsupported_postgres_version",
        ),
        (
            DatabaseProjectionState(17, False, None, None, None),
            "projection_not_read_only",
        ),
        (
            DatabaseProjectionState(17, True, incomplete, None, None),
            "active_release_invalid",
        ),
        (
            DatabaseProjectionState(17, True, None, incomplete, None),
            "previous_release_invalid",
        ),
    )

    for state, blocker_code in cases:
        result = build_projection_plan(rows, state)
        assert result["action"] == "blocked"
        assert [blocker["code"] for blocker in result["blockers"]] == [blocker_code]


def test_projection_has_its_own_cli_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called: dict[str, object] = {}

    def preview(repo_root: object, release: str) -> dict[str, object]:
        called.update(repo_root=repo_root, release=release)
        return {"status": "ready", "action": "noop"}

    monkeypatch.setattr(projection_module, "preview_projection", preview)

    assert projection_module.main(["corpus-v0.0.0"]) == 0
    assert called == {
        "repo_root": projection_module.REPO_ROOT,
        "release": "corpus-v0.0.0",
    }
    assert json.loads(capsys.readouterr().out) == {
        "status": "ready",
        "action": "noop",
    }


def _database_snapshot(database_url: str) -> dict[str, object]:
    with psycopg.connect(database_url) as connection:
        return {
            "releases": connection.execute(
                """
                SELECT release_tag, commit_sha, canonical_schema_versions,
                       projection_schema_version, import_state, imported_at
                FROM corpus_releases ORDER BY release_tag
                """
            ).fetchall(),
            "state": connection.execute(
                """
                SELECT singleton, active_release_tag, previous_release_tag,
                       activated_at
                FROM projection_state ORDER BY singleton
                """
            ).fetchall(),
            "counts": connection.execute(
                """
                SELECT (SELECT count(*) FROM sources),
                       (SELECT count(*) FROM acts),
                       (SELECT count(*) FROM provisions),
                       (SELECT count(*) FROM citations)
                """
            ).fetchone(),
        }


def test_projection_preview_is_read_only_on_postgres_17() -> None:
    database_url = os.environ.get("OPENACTS_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("OPENACTS_TEST_DATABASE_URL is not configured")
    before = _database_snapshot(database_url)

    result = preview_projection(
        ROOT,
        "corpus-v0.0.0",
        settings=ProjectionSettings(database_url=database_url),
    )

    assert result["status"] == "ready"
    assert result["action"] == "import_and_activate"
    assert result["transaction_read_only"] is True
    assert result["writes_performed"] is False
    assert result["release"]["counts"] == {
        "acts": 2,
        "sources": 2,
        "provisions": 3134,
        "citations": 0,
    }
    assert _database_snapshot(database_url) == before
