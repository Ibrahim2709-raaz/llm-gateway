"""Add reviewed provider pricing proposals.

Revision ID: 20261109_0016
Revises: 20261105_0015
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261109_0016"
down_revision: str | None = "20261105_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_pricing_proposals",
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("input_cost_per_million", sa.Numeric(14, 6), nullable=False),
        sa.Column("output_cost_per_million", sa.Numeric(14, 6), nullable=False),
        sa.Column("source_url", sa.String(length=2_048), nullable=False),
        sa.Column("rationale", sa.String(length=1_000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("proposed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("review_note", sa.String(length=1_000), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_provider_pricing_proposals_provider_pricing_proposal_status"),
        ),
        sa.CheckConstraint(
            "input_cost_per_million >= 0 AND output_cost_per_million >= 0",
            name=op.f(
                "ck_provider_pricing_proposals_provider_pricing_proposal_non_negative_cost"
            ),
        ),
        sa.CheckConstraint(
            "reviewed_by_user_id IS NULL OR proposed_by_user_id <> reviewed_by_user_id",
            name=op.f("ck_provider_pricing_proposals_provider_pricing_proposal_distinct_reviewer"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL) OR "
            "(status IN ('approved', 'rejected') AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL)",
            name=op.f("ck_provider_pricing_proposals_provider_pricing_proposal_review_state"),
        ),
        sa.ForeignKeyConstraint(
            ["proposed_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_pricing_proposals")),
    )
    for column in (
        "provider",
        "model",
        "status",
        "proposed_by_user_id",
        "reviewed_by_user_id",
        "reviewed_at",
    ):
        op.create_index(
            op.f(f"ix_provider_pricing_proposals_{column}"),
            "provider_pricing_proposals",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_provider_pricing_proposals_model_created",
        "provider_pricing_proposals",
        ["provider", "model", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("provider_pricing_proposals")
