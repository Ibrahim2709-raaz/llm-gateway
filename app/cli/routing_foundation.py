import argparse
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.session import session_factory
from app.routing.foundation import (
    FoundationInspection,
    inspect_routing_foundation,
    seed_routing_foundation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed or inspect the intelligent-routing data foundation."
    )
    parser.add_argument("command", choices=("seed", "show"))
    return parser


def format_inspection(inspection: FoundationInspection) -> str:
    lines = ["Intelligent routing foundation"]
    lines.append("Evaluation datasets:")
    if inspection.datasets:
        lines.extend(
            f"  - {dataset.name} {dataset.version} [{dataset.status}]"
            for dataset in inspection.datasets
        )
    else:
        lines.append("  - none")
    lines.append("Routing policies:")
    if inspection.policies:
        lines.extend(
            (
                f"  - {policy.version} [{policy.status}] "
                f"classifier={policy.classifier_version} scores={policy.score_version} "
                f"minimum_samples={policy.minimum_sample_count} "
                f"cold_start={policy.cold_start_mode}"
            )
            for policy in inspection.policies
        )
    else:
        lines.append("  - none")
    lines.append(f"Capability scores: {inspection.capability_score_count}")
    return "\n".join(lines)


async def run(command: str) -> str:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = session_factory(engine)
    try:
        if command == "seed":
            async with factory.begin() as session:
                result = await seed_routing_foundation(session)
            changes = []
            if result.dataset_created:
                changes.append("dataset")
            if result.policy_created:
                changes.append("policy")
            return "Seeded: " + (", ".join(changes) if changes else "already current")

        async with factory() as session:
            inspection = await inspect_routing_foundation(session)
        return format_inspection(inspection)
    finally:
        await engine.dispose()


def main() -> None:
    arguments = build_parser().parse_args()
    print(asyncio.run(run(arguments.command)))


if __name__ == "__main__":
    main()
