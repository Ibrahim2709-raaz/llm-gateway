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
    create_evaluation_run,
    inspect_evaluation_run,
    seed_smoke_evaluation_dataset,
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
                summary = await DatabaseEvaluationService(
                    session, resources.providers
                ).execute(arguments.run_id, resume=arguments.command == "resume")
            return json.dumps(asdict(summary), indent=2, default=str)
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
