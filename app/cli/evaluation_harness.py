import argparse
import asyncio
import json
from dataclasses import asdict
from uuid import UUID

from app.core.config import get_settings
from app.core.resources import close_resources, create_resources
from app.db.session import session_factory
from app.evaluations.operations import (
    SMOKE_DATASET_NAME,
    SMOKE_DATASET_VERSION,
    create_candidate_scores,
    create_evaluation_run,
    inspect_evaluation_run,
    seed_smoke_evaluation_dataset,
)
from app.evaluations.score_activation import (
    ScoreActivationAction,
    change_active_score_set,
    compare_score_version,
)
from app.evaluations.service import DatabaseEvaluationService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate reproducible model evaluations.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-smoke", help="Seed the synthetic smoke evaluation dataset.")

    create = commands.add_parser("create", help="Create a pinned pending evaluation run.")
    create.add_argument("--dataset", default=SMOKE_DATASET_NAME)
    create.add_argument("--dataset-version", default=SMOKE_DATASET_VERSION)
    create.add_argument("--provider", required=True)
    create.add_argument("--model", required=True)

    for command_name in ("run", "resume", "show"):
        command = commands.add_parser(command_name)
        command.add_argument("run_id", type=UUID)
    publish = commands.add_parser(
        "publish-scores", help="Create an immutable draft score version from a complete run."
    )
    publish.add_argument("run_id", type=UUID)
    publish.add_argument("--score-version", required=True)
    for command_name in ("activate-scores", "rollback-scores"):
        command = commands.add_parser(command_name)
        command.add_argument("--provider", required=True)
        command.add_argument("--model", required=True)
        command.add_argument("--score-version", required=True)
        command.add_argument("--actor", required=True)
        command.add_argument("--reason")
        command.add_argument("--valid-days", type=int, default=30)
        command.add_argument("--minimum-samples", type=int, default=5)
    compare = commands.add_parser("compare-scores")
    compare.add_argument("--provider", required=True)
    compare.add_argument("--model", required=True)
    compare.add_argument("--score-version", required=True)
    return parser


async def run_command(arguments: argparse.Namespace) -> str:
    resources = create_resources(get_settings())
    factory = session_factory(resources.database_engine)
    try:
        if arguments.command == "seed-smoke":
            async with factory.begin() as session:
                result = await seed_smoke_evaluation_dataset(session)
            return json.dumps(asdict(result), indent=2, default=str)
        if arguments.command == "create":
            async with factory.begin() as session:
                run = await create_evaluation_run(
                    session,
                    dataset_name=arguments.dataset,
                    dataset_version=arguments.dataset_version,
                    provider=arguments.provider,
                    model=arguments.model,
                )
            return json.dumps(
                {"run_id": str(run.id), "status": run.status}, indent=2
            )
        if arguments.command in {"run", "resume"}:
            async with factory() as session:
                run_summary = await DatabaseEvaluationService(
                    session, resources.providers
                ).execute(arguments.run_id, resume=arguments.command == "resume")
            return json.dumps(asdict(run_summary), indent=2, default=str)
        if arguments.command == "publish-scores":
            async with factory.begin() as session:
                score_summary = await create_candidate_scores(
                    session,
                    run_id=arguments.run_id,
                    score_version=arguments.score_version,
                )
            return json.dumps(asdict(score_summary), indent=2, default=str)
        if arguments.command in {"activate-scores", "rollback-scores"}:
            action: ScoreActivationAction = (
                "activate" if arguments.command == "activate-scores" else "rollback"
            )
            async with factory.begin() as session:
                activation = await change_active_score_set(
                    session,
                    provider=arguments.provider,
                    model=arguments.model,
                    score_version=arguments.score_version,
                    action=action,
                    actor=arguments.actor,
                    reason=arguments.reason,
                    valid_days=arguments.valid_days,
                    minimum_samples=arguments.minimum_samples,
                )
            return json.dumps(asdict(activation), indent=2, default=str)
        if arguments.command == "compare-scores":
            async with factory() as session:
                comparison = await compare_score_version(
                    session,
                    provider=arguments.provider,
                    model=arguments.model,
                    candidate_version=arguments.score_version,
                )
            return json.dumps(asdict(comparison), indent=2, default=str)
        async with factory() as session:
            inspection = await inspect_evaluation_run(session, arguments.run_id)
        return json.dumps(asdict(inspection), indent=2, default=str)
    finally:
        await close_resources(resources)


def main() -> None:
    arguments = build_parser().parse_args()
    print(asyncio.run(run_command(arguments)))


if __name__ == "__main__":
    main()
