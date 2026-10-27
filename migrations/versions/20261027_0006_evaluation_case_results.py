"""Add resumable per-case evaluation results.

Revision ID: 20261027_0006
Revises: 20261027_0005
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0006"
down_revision: str | None = "20261027_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_case_results",
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_case_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("actual_output", sa.JSON(), nullable=True),
        sa.Column("score", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('completed', 'failed', 'timed_out', 'refused')",
            name=op.f("ck_evaluation_case_results_evaluation_case_result_outcome"),
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name=op.f("ck_evaluation_case_results_evaluation_case_result_score_range"),
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND cost_usd >= 0 AND latency_ms >= 0",
            name=op.f("ck_evaluation_case_results_evaluation_case_result_non_negative_usage"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_case_id"],
            ["evaluation_cases.id"],
            name=op.f("fk_evaluation_case_results_evaluation_case_id_evaluation_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name=op.f("fk_evaluation_case_results_evaluation_run_id_evaluation_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_case_results")),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "evaluation_case_id",
            name="uq_evaluation_case_result_run_case",
        ),
    )
    op.create_index(
        op.f("ix_evaluation_case_results_evaluation_case_id"),
        "evaluation_case_results",
        ["evaluation_case_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evaluation_case_results_evaluation_run_id"),
        "evaluation_case_results",
        ["evaluation_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evaluation_case_results_outcome"),
        "evaluation_case_results",
        ["outcome"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_evaluation_case_results_outcome"),
        table_name="evaluation_case_results",
    )
    op.drop_index(
        op.f("ix_evaluation_case_results_evaluation_run_id"),
        table_name="evaluation_case_results",
    )
    op.drop_index(
        op.f("ix_evaluation_case_results_evaluation_case_id"),
        table_name="evaluation_case_results",
    )
    op.drop_table("evaluation_case_results")
