"""Add persistent intelligent-routing rollout controls.

Revision ID: 20261109_0017
Revises: 20261109_0016
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261109_0017"
down_revision: str | None = "20261109_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "routing_rollout_configurations",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("canary_percent", sa.Integer(), nullable=False),
        sa.Column("customer_allowlist", sa.JSON(), nullable=False),
        sa.Column("required_policy_version", sa.String(length=64), nullable=True),
        sa.Column("required_score_version", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by_actor", sa.String(length=200), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('baseline', 'shadow', 'live')",
            name=op.f(
                "ck_routing_rollout_configurations_routing_rollout_configuration_mode"
            ),
        ),
        sa.CheckConstraint(
            "canary_percent >= 0 AND canary_percent <= 100",
            name=op.f(
                "ck_routing_rollout_configurations_routing_rollout_configuration_canary_range"
            ),
        ),
        sa.CheckConstraint(
            "version > 0",
            name=op.f(
                "ck_routing_rollout_configurations_routing_rollout_configuration_version"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routing_rollout_configurations")),
        sa.UniqueConstraint(
            "name", name="uq_routing_rollout_configuration_name"
        ),
    )
    op.create_table(
        "routing_rollout_changes",
        sa.Column("configuration_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("previous_configuration", sa.JSON(), nullable=False),
        sa.Column("new_configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["routing_rollout_configurations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routing_rollout_changes")),
    )
    for column in ("configuration_id", "action", "created_at"):
        op.create_index(
            op.f(f"ix_routing_rollout_changes_{column}"),
            "routing_rollout_changes",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("routing_rollout_changes")
    op.drop_table("routing_rollout_configurations")
