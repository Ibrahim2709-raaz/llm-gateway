import argparse
import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.session import session_factory
from app.routing.control_plane import (
    DatabaseRoutingControlResolver,
    change_routing_control,
    emergency_baseline,
    model_runtime,
    serialize_runtime,
)
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
    commands.add_parser("rollout-show", help="Show the effective rollout control state.")
    rollout = commands.add_parser("rollout-set", help="Change the persistent rollout state.")
    rollout.add_argument("--mode", required=True, choices=("baseline", "shadow", "live"))
    rollout.add_argument("--canary-percent", type=int, default=0)
    rollout.add_argument("--allow-client", action="append", default=[])
    rollout.add_argument("--policy-version")
    rollout.add_argument("--score-version")
    rollout.add_argument("--actor", required=True)
    rollout.add_argument("--reason", required=True)
    emergency = commands.add_parser(
        "emergency-baseline", help="Immediately return all routing to the baseline."
    )
    emergency.add_argument("--actor", required=True)
    emergency.add_argument("--reason", required=True)
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
    mode: str | None = None,
    canary_percent: int = 0,
    allow_clients: list[str] | None = None,
    policy_version: str | None = None,
    score_version: str | None = None,
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

        if command == "rollout-show":
            async with factory() as session:
                runtime = await DatabaseRoutingControlResolver(session, settings).load()
            return f"Routing rollout: {serialize_runtime(runtime)}"

        if command == "rollout-set":
            if mode is None or actor is None or reason is None:
                raise ValueError("rollout-set requires mode, actor, and reason")
            async with factory.begin() as session:
                result = await change_routing_control(
                    session,
                    mode=mode,  # type: ignore[arg-type]
                    canary_percent=canary_percent,
                    customer_allowlist=frozenset(
                        UUID(value) for value in (allow_clients or [])
                    ),
                    required_policy_version=policy_version,
                    required_score_version=score_version,
                    actor=actor,
                    reason=reason,
                )
            runtime = model_runtime(result.configuration)
            return f"Routing rollout updated: {serialize_runtime(runtime)}"

        if command == "emergency-baseline":
            if actor is None or reason is None:
                raise ValueError("emergency-baseline requires actor and reason")
            async with factory.begin() as session:
                result = await emergency_baseline(session, actor=actor, reason=reason)
            runtime = model_runtime(result.configuration)
            return f"Emergency baseline enabled: {serialize_runtime(runtime)}"

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
                mode=getattr(arguments, "mode", None),
                canary_percent=getattr(arguments, "canary_percent", 0),
                allow_clients=getattr(arguments, "allow_client", None),
                policy_version=getattr(arguments, "policy_version", None),
                score_version=getattr(arguments, "score_version", None),
            )
        )
    )


if __name__ == "__main__":
    main()
