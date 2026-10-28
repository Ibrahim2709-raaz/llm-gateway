"""Add score-set activation audit history.

Revision ID: 20261027_0007
Revises: 20261027_0006
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0007"
down_revision: str | None = "20261027_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "score_set_activations",
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("score_version", sa.String(length=64), nullable=False),
        sa.Column("previous_score_version", sa.String(length=64), nullable=True),
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
            name=op.f("ck_score_set_activations_score_set_activation_action"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name=op.f("fk_score_set_activations_evaluation_run_id_evaluation_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_score_set_activations")),
    )
    op.create_index(
        op.f("ix_score_set_activations_evaluation_run_id"),
        "score_set_activations",
        ["evaluation_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_score_set_activations_model"),
        "score_set_activations",
        ["model"],
        unique=False,
    )
    op.create_index(
        op.f("ix_score_set_activations_provider"),
        "score_set_activations",
        ["provider"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_score_set_activations_provider"), table_name="score_set_activations")
    op.drop_index(op.f("ix_score_set_activations_model"), table_name="score_set_activations")
    op.drop_index(
        op.f("ix_score_set_activations_evaluation_run_id"),
        table_name="score_set_activations",
    )
    op.drop_table("score_set_activations")
