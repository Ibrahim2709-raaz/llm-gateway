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
from app.routing.policy_activation import ActivationAction, change_active_policy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed or inspect the intelligent-routing data foundation."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="Create the baseline dataset and policy if absent.")
    commands.add_parser("show", help="Inspect datasets, policies, scores, and activation history.")
    for action in ("activate", "rollback"):
        command = commands.add_parser(action, help=f"{action.title()} a routing policy version.")
        command.add_argument("version")
        command.add_argument("--actor", required=True)
        command.add_argument("--reason")
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
    lines.append("Recent policy activations:")
    if inspection.activations:
        lines.extend(
            (
                f"  - {activation.action} {activation.policy_version} "
                f"from={activation.previous_policy_version or 'none'} "
                f"actor={activation.actor}"
            )
            for activation in inspection.activations
        )
    else:
        lines.append("  - none")
    return "\n".join(lines)


async def run(
    command: str,
    *,
    version: str | None = None,
    actor: str | None = None,
    reason: str | None = None,
) -> str:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = session_factory(engine)
    try:
        if command == "seed":
            async with factory.begin() as session:
                seed_result = await seed_routing_foundation(session)
            changes = []
            if seed_result.dataset_created:
                changes.append("dataset")
            if seed_result.policy_created:
                changes.append("policy")
            return "Seeded: " + (", ".join(changes) if changes else "already current")

        if command in {"activate", "rollback"}:
            if version is None or actor is None:
                raise ValueError(f"{command} requires a policy version and actor")
            action: ActivationAction = "activate" if command == "activate" else "rollback"
            async with factory.begin() as session:
                activation_result = await change_active_policy(
                    session,
                    version=version,
                    action=action,
                    actor=actor,
                    reason=reason,
                )
            verb = "Activated" if action == "activate" else "Rolled back to"
            previous = activation_result.previous_policy_version or "none"
            return f"{verb} policy {activation_result.policy_version}; previous={previous}"

        async with factory() as session:
            inspection = await inspect_routing_foundation(session)
        return format_inspection(inspection)
    finally:
        await engine.dispose()


def main() -> None:
    arguments = build_parser().parse_args()
    print(
        asyncio.run(
            run(
                arguments.command,
                version=getattr(arguments, "version", None),
                actor=getattr(arguments, "actor", None),
                reason=getattr(arguments, "reason", None),
            )
        )
    )


if __name__ == "__main__":
    main()
