"""Add organization routing preferences.

Revision ID: 20261029_0013
Revises: 20261029_0012
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_0013"
down_revision: str | None = "20261029_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organization_routing_preferences",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "strategy IN ('balanced', 'cost', 'latency', 'quality')",
            name=op.f(
                "ck_organization_routing_preferences_"
                "organization_routing_preference_strategy"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f(
                "fk_organization_routing_preferences_organization_id_organizations"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f(
                "fk_organization_routing_preferences_updated_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_routing_preferences")),
        sa.UniqueConstraint(
            "organization_id", name="uq_organization_routing_preference"
        ),
    )
    op.create_index(
        op.f("ix_organization_routing_preferences_organization_id"),
        "organization_routing_preferences",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_organization_routing_preferences_updated_by_user_id"),
        "organization_routing_preferences",
        ["updated_by_user_id"],
    )


def downgrade() -> None:
    op.drop_table("organization_routing_preferences")
