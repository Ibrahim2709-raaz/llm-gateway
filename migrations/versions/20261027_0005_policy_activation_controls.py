"""Add policy activation controls and audit history.

Revision ID: 20261027_0005
Revises: 20261027_0004
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0005"
down_revision: str | None = "20261027_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_routing_policy_single_active",
        "routing_policy_versions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "routing_policy_activations",
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("previous_policy_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "action IN ('activate', 'rollback')",
            name=op.f("ck_routing_policy_activations_routing_policy_activation_action"),
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["routing_policy_versions.id"],
            name=op.f(
                "fk_routing_policy_activations_policy_id_routing_policy_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_policy_id"],
            ["routing_policy_versions.id"],
            name=op.f(
                "fk_routing_policy_activations_previous_policy_id_routing_policy_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routing_policy_activations")),
    )
    op.create_index(
        op.f("ix_routing_policy_activations_policy_id"),
        "routing_policy_activations",
        ["policy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_routing_policy_activations_previous_policy_id"),
        "routing_policy_activations",
        ["previous_policy_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_routing_policy_activations_previous_policy_id"),
        table_name="routing_policy_activations",
    )
    op.drop_index(
        op.f("ix_routing_policy_activations_policy_id"),
        table_name="routing_policy_activations",
    )
    op.drop_table("routing_policy_activations")
    op.drop_index("uq_routing_policy_single_active", table_name="routing_policy_versions")
