"""Create intelligent routing foundation tables.

Revision ID: 20261027_0003
Revises: 20261021_0002
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0003"
down_revision: str | None = "20261021_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_datasets",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("license_name", sa.String(length=128), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name=op.f("ck_evaluation_datasets_evaluation_dataset_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_datasets")),
        sa.UniqueConstraint(
            "name", "version", name="uq_evaluation_dataset_name_version"
        ),
    )
    op.create_index(
        op.f("ix_evaluation_datasets_status"),
        "evaluation_datasets",
        ["status"],
        unique=False,
    )
    op.create_table(
        "evaluation_cases",
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("task_category", sa.String(length=32), nullable=False),
        sa.Column("difficulty", sa.String(length=32), nullable=False),
        sa.Column("prompt", sa.JSON(), nullable=False),
        sa.Column("expected_output", sa.JSON(), nullable=True),
        sa.Column("scoring_configuration", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "task_category IN ('coding', 'reasoning', 'summarization', 'extraction', "
            "'general_chat')",
            name=op.f("ck_evaluation_cases_evaluation_case_task_category"),
        ),
        sa.CheckConstraint(
            "difficulty IN ('simple', 'medium', 'complex')",
            name=op.f("ck_evaluation_cases_evaluation_case_difficulty"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["evaluation_datasets.id"],
            name=op.f("fk_evaluation_cases_dataset_id_evaluation_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_cases")),
        sa.UniqueConstraint(
            "dataset_id", "external_id", name="uq_evaluation_case_external_id"
        ),
    )
    op.create_index(
        op.f("ix_evaluation_cases_dataset_id"),
        "evaluation_cases",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evaluation_cases_task_category"),
        "evaluation_cases",
        ["task_category"],
        unique=False,
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("provider_config_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("runner_version", sa.String(length=64), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("aggregate_metrics", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name=op.f("ck_evaluation_runs_evaluation_run_status"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["evaluation_datasets.id"],
            name=op.f("fk_evaluation_runs_dataset_id_evaluation_datasets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_config_id"],
            ["provider_configs.id"],
            name=op.f("fk_evaluation_runs_provider_config_id_provider_configs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
    )
    op.create_index(
        op.f("ix_evaluation_runs_dataset_id"),
        "evaluation_runs",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evaluation_runs_provider_config_id"),
        "evaluation_runs",
        ["provider_config_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evaluation_runs_status"), "evaluation_runs", ["status"], unique=False
    )
    op.create_table(
        "model_capability_scores",
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("provider_config_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("score_version", sa.String(length=64), nullable=False),
        sa.Column("task_category", sa.String(length=32), nullable=False),
        sa.Column("difficulty", sa.String(length=32), nullable=False),
        sa.Column("quality_score", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("score_variance", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "task_category IN ('coding', 'reasoning', 'summarization', 'extraction', "
            "'general_chat')",
            name=op.f("ck_model_capability_scores_model_capability_score_task_category"),
        ),
        sa.CheckConstraint(
            "difficulty IN ('simple', 'medium', 'complex')",
            name=op.f("ck_model_capability_scores_model_capability_score_difficulty"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name=op.f("ck_model_capability_scores_model_capability_score_status"),
        ),
        sa.CheckConstraint(
            "quality_score >= 0 AND quality_score <= 1",
            name=op.f("ck_model_capability_scores_model_capability_score_quality_range"),
        ),
        sa.CheckConstraint(
            "sample_count > 0",
            name=op.f("ck_model_capability_scores_model_capability_score_sample_count"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name=op.f("fk_model_capability_scores_evaluation_run_id_evaluation_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_config_id"],
            ["provider_configs.id"],
            name=op.f("fk_model_capability_scores_provider_config_id_provider_configs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_capability_scores")),
        sa.UniqueConstraint(
            "provider",
            "model",
            "score_version",
            "task_category",
            "difficulty",
            name="uq_model_capability_score_version",
        ),
    )
    op.create_index(
        op.f("ix_model_capability_scores_evaluation_run_id"),
        "model_capability_scores",
        ["evaluation_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_model_capability_scores_provider_config_id"),
        "model_capability_scores",
        ["provider_config_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_model_capability_scores_score_version"),
        "model_capability_scores",
        ["score_version"],
        unique=False,
    )
    op.create_index(
        op.f("ix_model_capability_scores_status"),
        "model_capability_scores",
        ["status"],
        unique=False,
    )
    op.create_table(
        "routing_policy_versions",
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("classifier_version", sa.String(length=64), nullable=False),
        sa.Column("score_version", sa.String(length=64), nullable=False),
        sa.Column("strategy_weights", sa.JSON(), nullable=False),
        sa.Column("minimum_quality", sa.JSON(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name=op.f("ck_routing_policy_versions_routing_policy_version_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routing_policy_versions")),
        sa.UniqueConstraint("version", name="uq_routing_policy_version"),
    )
    op.create_index(
        op.f("ix_routing_policy_versions_status"),
        "routing_policy_versions",
        ["status"],
        unique=False,
    )
    op.create_table(
        "routing_decision_snapshots",
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("routing_policy_id", sa.Uuid(), nullable=True),
        sa.Column("task_category", sa.String(length=32), nullable=False),
        sa.Column("difficulty", sa.String(length=32), nullable=False),
        sa.Column("classification_confidence", sa.Numeric(precision=7, scale=6), nullable=False),
        sa.Column("classifier_version", sa.String(length=64), nullable=False),
        sa.Column("score_version", sa.String(length=64), nullable=False),
        sa.Column("selected_provider", sa.String(length=64), nullable=False),
        sa.Column("selected_model", sa.String(length=128), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "task_category IN ('coding', 'reasoning', 'summarization', 'extraction', "
            "'general_chat')",
            name=op.f("ck_routing_decision_snapshots_routing_decision_task_category"),
        ),
        sa.CheckConstraint(
            "difficulty IN ('simple', 'medium', 'complex')",
            name=op.f("ck_routing_decision_snapshots_routing_decision_difficulty"),
        ),
        sa.CheckConstraint(
            "classification_confidence >= 0 AND classification_confidence <= 1",
            name=op.f("ck_routing_decision_snapshots_routing_decision_confidence_range"),
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_routing_decision_snapshots_client_id_clients"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["routing_policy_id"],
            ["routing_policy_versions.id"],
            name=op.f("fk_routing_decision_snapshots_routing_policy_id_routing_policy_versions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routing_decision_snapshots")),
        sa.UniqueConstraint(
            "request_id", name=op.f("uq_routing_decision_snapshots_request_id")
        ),
    )
    op.create_index(
        op.f("ix_routing_decision_snapshots_client_id"),
        "routing_decision_snapshots",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_routing_decision_snapshots_created_at"),
        "routing_decision_snapshots",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_routing_decision_snapshots_routing_policy_id"),
        "routing_decision_snapshots",
        ["routing_policy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_routing_decision_snapshots_task_category"),
        "routing_decision_snapshots",
        ["task_category"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("routing_decision_snapshots")
    op.drop_table("routing_policy_versions")
    op.drop_table("model_capability_scores")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluation_cases")
    op.drop_table("evaluation_datasets")
