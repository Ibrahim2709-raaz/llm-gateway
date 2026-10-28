"""Add evidence eligibility and cold-start policy.

Revision ID: 20261027_0004
Revises: 20261027_0003
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0004"
down_revision: str | None = "20261027_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_capability_scores",
        sa.Column(
            "capabilities",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "model_capability_scores",
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("model_capability_scores", "capabilities", server_default=None)

    op.add_column(
        "routing_policy_versions",
        sa.Column(
            "minimum_sample_count",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
    )
    op.add_column(
        "routing_policy_versions",
        sa.Column(
            "cold_start_mode",
            sa.String(length=32),
            server_default=sa.text("'static_profile'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_routing_policy_versions_routing_policy_cold_start_mode"),
        "routing_policy_versions",
        "cold_start_mode IN ('static_profile', 'exclude', 'explicit_only')",
    )
    op.create_check_constraint(
        op.f("ck_routing_policy_versions_routing_policy_minimum_sample_count"),
        "routing_policy_versions",
        "minimum_sample_count > 0",
    )
    op.alter_column("routing_policy_versions", "minimum_sample_count", server_default=None)
    op.alter_column("routing_policy_versions", "cold_start_mode", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_routing_policy_versions_routing_policy_minimum_sample_count"),
        "routing_policy_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_routing_policy_versions_routing_policy_cold_start_mode"),
        "routing_policy_versions",
        type_="check",
    )
    op.drop_column("routing_policy_versions", "cold_start_mode")
    op.drop_column("routing_policy_versions", "minimum_sample_count")
    op.drop_column("model_capability_scores", "valid_until")
    op.drop_column("model_capability_scores", "capabilities")
