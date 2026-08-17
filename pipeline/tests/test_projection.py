import copy
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

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


def _release_repo(
    tmp_path: Path, release_tag: str = "corpus-v0.0.0"
) -> tuple[Path, Path]:
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
    _git(repo, "tag", "-a", release_tag, "-m", "test release")
    return repo, act_dir


def _add_second_release(repo: Path, act_dir: Path) -> None:
    act = _load("act.json")
    act["titles"]["official"] = "Example Act 2023, revised projection"
    _write_json(act_dir / "act.json", act)
    _git(repo, "add", "corpus")
    _git(repo, "commit", "-q", "-m", "second test release")
    _git(repo, "tag", "-a", "corpus-v0.2.0", "-m", "second test release")


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
        called.update(mode="preview", repo_root=repo_root, release=release)
        return {"status": "ready", "action": "noop"}

    def execute(
        repo_root: object, release: str, *, allow_bootstrap: bool
    ) -> dict[str, object]:
        called.update(
            mode="execute",
            repo_root=repo_root,
            release=release,
            allow_bootstrap=allow_bootstrap,
        )
        return {"status": "success", "action": "noop"}

    monkeypatch.setattr(projection_module, "preview_projection", preview)
    monkeypatch.setattr(
        projection_module, "execute_projection", execute, raising=False
    )

    assert projection_module.main(["corpus-v0.0.0"]) == 0
    assert called == {
        "mode": "preview",
        "repo_root": projection_module.REPO_ROOT,
        "release": "corpus-v0.0.0",
    }
    assert json.loads(capsys.readouterr().out) == {
        "status": "ready",
        "action": "noop",
    }

    assert (
        projection_module.main(
            ["corpus-v0.0.0", "--execute", "--allow-bootstrap"]
        )
        == 0
    )
    assert called == {
        "mode": "execute",
        "repo_root": projection_module.REPO_ROOT,
        "release": "corpus-v0.0.0",
        "allow_bootstrap": True,
    }
    assert json.loads(capsys.readouterr().out) == {
        "status": "success",
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


@pytest.fixture
def projection_database_url() -> str:
    database_url = os.environ.get("OPENACTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("OPENACTS_TEST_DATABASE_URL is not configured")
    schema_name = f"projection_test_{uuid.uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name))
        )
    isolated_url = make_conninfo(
        database_url, options=f"-csearch_path={schema_name}"
    )
    try:
        with psycopg.connect(isolated_url, autocommit=True) as connection:
            connection.execute(
                (ROOT / "api/sql/001_projection.sql").read_text(encoding="utf-8")
            )
        yield isolated_url
    finally:
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )


def test_projection_execution_requires_explicit_bootstrap_override(
    tmp_path: Path,
) -> None:
    repo, _act_dir = _release_repo(tmp_path)
    with pytest.raises(PipelineError) as blocked:
        projection_module.execute_projection(
            repo,
            "corpus-v0.0.0",
            settings=ProjectionSettings(database_url="postgresql://unused"),
        )

    assert blocked.value.code == "bootstrap_release_blocked"


def test_projection_preview_is_read_only_on_postgres_17(
    projection_database_url: str,
) -> None:
    database_url = projection_database_url
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


def test_projection_execute_imports_reuses_and_reactivates(
    tmp_path: Path, projection_database_url: str
) -> None:
    repo, act_dir = _release_repo(tmp_path, "corpus-v0.1.0")
    _add_second_release(repo, act_dir)
    settings = ProjectionSettings(database_url=projection_database_url)

    imported = projection_module.execute_projection(
        repo, "corpus-v0.1.0", settings=settings
    )
    first_snapshot = _database_snapshot(projection_database_url)
    reused = projection_module.execute_projection(
        repo, "corpus-v0.1.0", settings=settings
    )
    assert reused["action"] == "noop"
    assert reused["writes_performed"] is False
    assert _database_snapshot(projection_database_url) == first_snapshot

    projection_module.execute_projection(repo, "corpus-v0.2.0", settings=settings)
    rollback = projection_module.execute_projection(
        repo, "corpus-v0.1.0", settings=settings
    )

    assert imported["action"] == "import_and_activate"
    assert first_snapshot["counts"] == (1, 1, 3, 1)
    assert rollback["action"] == "activate_existing"
    assert _database_snapshot(projection_database_url)["state"][0][1:3] == (
        "corpus-v0.1.0",
        "corpus-v0.2.0",
    )


def test_projection_execute_rolls_back_partial_import(
    tmp_path: Path,
    projection_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, act_dir = _release_repo(tmp_path, "corpus-v0.1.0")
    _add_second_release(repo, act_dir)
    settings = ProjectionSettings(database_url=projection_database_url)
    projection_module.execute_projection(repo, "corpus-v0.1.0", settings=settings)
    before = _database_snapshot(projection_database_url)
    rows = build_projection_rows(load_tagged_corpus(repo, "corpus-v0.2.0"))
    broken_citation = replace(rows.citations[0], target_act_id="missing-act")
    broken_rows = replace(rows, citations=(broken_citation,))
    monkeypatch.setattr(
        projection_module, "build_projection_rows", lambda _tagged: broken_rows
    )

    with pytest.raises(PipelineError) as failed:
        projection_module.execute_projection(
            repo, "corpus-v0.2.0", settings=settings
        )

    assert failed.value.code == "projection_import_failed"
    assert _database_snapshot(projection_database_url) == before


def test_projection_execute_fails_fast_when_another_writer_holds_the_lock(
    tmp_path: Path, projection_database_url: str
) -> None:
    repo, _act_dir = _release_repo(tmp_path, "corpus-v0.1.0")
    settings = ProjectionSettings(database_url=projection_database_url)

    with psycopg.connect(projection_database_url) as lock_connection:
        lock_connection.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (projection_module.PROJECTION_ADVISORY_LOCK_ID,),
        )
        with pytest.raises(PipelineError) as busy:
            projection_module.execute_projection(
                repo, "corpus-v0.1.0", settings=settings
            )

    assert busy.value.code == "projection_busy"
    assert busy.value.retryable is True
