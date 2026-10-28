import argparse
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.cli import evaluation_harness


@dataclass(frozen=True)
class Result:
    status: str


class AsyncSessionContext:
    def __init__(self, session: Mock) -> None:
        self.session = session

    async def __aenter__(self) -> Mock:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSessionFactory:
    def __init__(self) -> None:
        self.session = Mock()

    def begin(self) -> AsyncSessionContext:
        return AsyncSessionContext(self.session)

    def __call__(self) -> AsyncSessionContext:
        return AsyncSessionContext(self.session)


@pytest.mark.anyio
async def test_evaluation_cli_dispatches_every_operator_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = SimpleNamespace(database_engine=object(), providers=())
    factory = FakeSessionFactory()
    close_resources = AsyncMock()
    seed = AsyncMock(return_value=Result("seeded"))
    create = AsyncMock(return_value=SimpleNamespace(id=uuid4(), status="pending"))
    execute = AsyncMock(return_value=Result("complete"))
    publish = AsyncMock(return_value=Result("draft"))
    activate = AsyncMock(return_value=Result("active"))
    compare = AsyncMock(return_value=Result("compared"))
    inspect = AsyncMock(return_value=Result("inspected"))
    service = Mock(execute=execute)

    monkeypatch.setattr(evaluation_harness, "get_settings", Mock(return_value=Mock()))
    monkeypatch.setattr(
        evaluation_harness, "create_resources", Mock(return_value=resources)
    )
    monkeypatch.setattr(evaluation_harness, "session_factory", Mock(return_value=factory))
    monkeypatch.setattr(evaluation_harness, "close_resources", close_resources)
    monkeypatch.setattr(evaluation_harness, "seed_smoke_evaluation_dataset", seed)
    monkeypatch.setattr(evaluation_harness, "create_evaluation_run", create)
    monkeypatch.setattr(evaluation_harness, "DatabaseEvaluationService", Mock(return_value=service))
    monkeypatch.setattr(evaluation_harness, "create_candidate_scores", publish)
    monkeypatch.setattr(evaluation_harness, "change_active_score_set", activate)
    monkeypatch.setattr(evaluation_harness, "compare_score_version", compare)
    monkeypatch.setattr(evaluation_harness, "inspect_evaluation_run", inspect)

    run_id = uuid4()
    commands = [
        argparse.Namespace(command="seed-smoke"),
        argparse.Namespace(
            command="create",
            dataset="smoke",
            dataset_version="1.0.0",
            provider="mock",
            model="mock-1",
        ),
        argparse.Namespace(command="run", run_id=run_id),
        argparse.Namespace(command="resume", run_id=run_id),
        argparse.Namespace(command="publish-scores", run_id=run_id, score_version="scores-v2"),
        argparse.Namespace(
            command="activate-scores",
            provider="mock",
            model="mock-1",
            score_version="scores-v2",
            actor="release-bot",
            reason="approved",
            valid_days=30,
            minimum_samples=5,
        ),
        argparse.Namespace(
            command="rollback-scores",
            provider="mock",
            model="mock-1",
            score_version="scores-v1",
            actor="on-call",
            reason="incident",
            valid_days=30,
            minimum_samples=5,
        ),
        argparse.Namespace(
            command="compare-scores",
            provider="mock",
            model="mock-1",
            score_version="scores-v2",
        ),
        argparse.Namespace(command="show", run_id=run_id),
    ]

    outputs = [await evaluation_harness.run_command(command) for command in commands]

    assert all(output.startswith("{") for output in outputs)
    assert execute.await_count == 2
    assert execute.await_args_list[0].kwargs == {"resume": False}
    assert execute.await_args_list[1].kwargs == {"resume": True}
    assert activate.await_args_list[0].kwargs["action"] == "activate"
    assert activate.await_args_list[1].kwargs["action"] == "rollback"
    assert close_resources.await_count == len(commands)


def test_evaluation_cli_main_prints_command_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = argparse.Namespace(command="seed-smoke")
    parser = Mock()
    parser.parse_args.return_value = arguments
    monkeypatch.setattr(evaluation_harness, "build_parser", Mock(return_value=parser))
    monkeypatch.setattr(
        evaluation_harness,
        "run_command",
        AsyncMock(return_value='{"status": "seeded"}'),
    )

    evaluation_harness.main()

    assert capsys.readouterr().out.strip() == '{"status": "seeded"}'
