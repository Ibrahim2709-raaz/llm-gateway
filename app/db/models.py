from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.identity.roles import (
    AUDIT_ACTOR_TYPES,
    MEMBERSHIP_STATUSES,
    ORGANIZATION_ROLES,
    ORGANIZATION_STATUSES,
    PLATFORM_ROLES,
    SERVICE_ACCOUNT_ROLES,
    SERVICE_ACCOUNT_STATUSES,
    USER_ACTION_TOKEN_PURPOSES,
    USER_STATUSES,
)
from app.routing.taxonomy import (
    COLD_START_MODES,
    EVALUATION_CASE_OUTCOMES,
    EVALUATION_RUN_STATUSES,
    POLICY_ACTIVATION_ACTIONS,
    TASK_CATEGORIES,
    TASK_DIFFICULTIES,
    VERSION_STATUSES,
)


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "api_keys"

    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("client_id", name="uq_organizations_client_id"),
        CheckConstraint(
            f"status IN ({_sql_values(ORGANIZATION_STATUSES)})",
            name="organization_status",
        ),
    )

    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_sql_values(USER_STATUSES)})",
            name="user_status",
        ),
        CheckConstraint(
            f"platform_role IS NULL OR platform_role IN ({_sql_values(PLATFORM_ROLES)})",
            name="user_platform_role",
        ),
    )

    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    platform_role: Mapped[str | None] = mapped_column(String(32), index=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrowserSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "browser_sessions"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    active_organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class UserActionToken(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_action_tokens"
    __table_args__ = (
        CheckConstraint(
            f"purpose IN ({_sql_values(USER_ACTION_TOKEN_PURPOSES)})",
            name="user_action_token_purpose",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class OrganizationMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_organization_membership_user"
        ),
        CheckConstraint(
            f"role IN ({_sql_values(ORGANIZATION_ROLES)})",
            name="organization_membership_role",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(MEMBERSHIP_STATUSES)})",
            name="organization_membership_status",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)


class OrganizationInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organization_invitations"
    __table_args__ = (
        CheckConstraint(
            f"role IN ({_sql_values(ORGANIZATION_ROLES)})",
            name="organization_invitation_role",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "service_accounts"
    __table_args__ = (
        CheckConstraint(
            f"role IN ({_sql_values(SERVICE_ACCOUNT_ROLES)})",
            name="service_account_role",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(SERVICE_ACCOUNT_STATUSES)})",
            name="service_account_status",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="developer", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            f"actor_type IN ({_sql_values(AUDIT_ACTOR_TYPES)})",
            name="audit_event_actor_type",
        ),
        CheckConstraint(
            "(actor_type = 'system' AND actor_id IS NULL) OR "
            "(actor_type <> 'system' AND actor_id IS NOT NULL)",
            name="audit_event_actor_identity",
        ),
        Index("ix_audit_events_organization_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor_id: Mapped[UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(200))
    request_id: Mapped[UUID | None] = mapped_column(index=True)
    event_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class ProviderConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_configs"
    __table_args__ = (UniqueConstraint("provider", "model", name="uq_provider_model"),)

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    input_cost_per_million: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), default=Decimal("0"), nullable=False
    )
    output_cost_per_million: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), default=Decimal("0"), nullable=False
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class BudgetLimit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "budget_limits"
    __table_args__ = (UniqueConstraint("client_id", "period", name="uq_client_budget_period"),)

    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    limit_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    alert_threshold_percent: Mapped[int] = mapped_column(Integer, default=80, nullable=False)


class UsageRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_records_client_created", "client_id", "created_at"),)

    request_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    provider_config_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provider_configs.id", ondelete="SET NULL")
    )
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class EvaluationDataset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_evaluation_dataset_name_version"),
        CheckConstraint(
            f"status IN ({_sql_values(VERSION_STATUSES)})",
            name="evaluation_dataset_status",
        ),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    license_name: Mapped[str | None] = mapped_column(String(128))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class EvaluationCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (
        UniqueConstraint("dataset_id", "external_id", name="uq_evaluation_case_external_id"),
        CheckConstraint(
            f"task_category IN ({_sql_values(TASK_CATEGORIES)})",
            name="evaluation_case_task_category",
        ),
        CheckConstraint(
            f"difficulty IN ({_sql_values(TASK_DIFFICULTIES)})",
            name="evaluation_case_difficulty",
        ),
    )

    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    task_category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    difficulty: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expected_output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    scoring_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    source: Mapped[str | None] = mapped_column(String(500))


class EvaluationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_sql_values(EVALUATION_RUN_STATUSES)})",
            name="evaluation_run_status",
        ),
    )

    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider_config_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provider_configs.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    runner_version: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    aggregate_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationCaseResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_run_id",
            "evaluation_case_id",
            name="uq_evaluation_case_result_run_case",
        ),
        CheckConstraint(
            f"outcome IN ({_sql_values(EVALUATION_CASE_OUTCOMES)})",
            name="evaluation_case_result_outcome",
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="evaluation_case_result_score_range",
        ),
        CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND cost_usd >= 0 AND latency_ms >= 0",
            name="evaluation_case_result_non_negative_usage",
        ),
    )

    evaluation_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evaluation_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actual_output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), default=Decimal("0"), nullable=False
    )
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))


class ModelCapabilityScore(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_capability_scores"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "model",
            "score_version",
            "task_category",
            "difficulty",
            name="uq_model_capability_score_version",
        ),
        CheckConstraint(
            f"task_category IN ({_sql_values(TASK_CATEGORIES)})",
            name="model_capability_score_task_category",
        ),
        CheckConstraint(
            f"difficulty IN ({_sql_values(TASK_DIFFICULTIES)})",
            name="model_capability_score_difficulty",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(VERSION_STATUSES)})",
            name="model_capability_score_status",
        ),
        CheckConstraint(
            "quality_score >= 0 AND quality_score <= 1",
            name="model_capability_score_quality_range",
        ),
        CheckConstraint("sample_count > 0", name="model_capability_score_sample_count"),
    )

    evaluation_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider_config_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provider_configs.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_category: Mapped[str] = mapped_column(String(32), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_score: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    score_variance: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScoreSetActivation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "score_set_activations"
    __table_args__ = (
        CheckConstraint(
            f"action IN ({_sql_values(POLICY_ACTIVATION_ACTIONS)})",
            name="score_set_activation_action",
        ),
    )

    evaluation_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_score_version: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RoutingPolicyVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "routing_policy_versions"
    __table_args__ = (
        UniqueConstraint("version", name="uq_routing_policy_version"),
        CheckConstraint(
            f"status IN ({_sql_values(VERSION_STATUSES)})",
            name="routing_policy_version_status",
        ),
        CheckConstraint(
            f"cold_start_mode IN ({_sql_values(COLD_START_MODES)})",
            name="routing_policy_cold_start_mode",
        ),
        CheckConstraint(
            "minimum_sample_count > 0",
            name="routing_policy_minimum_sample_count",
        ),
        Index(
            "uq_routing_policy_single_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    classifier_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_weights: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    minimum_quality: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    minimum_sample_count: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    cold_start_mode: Mapped[str] = mapped_column(
        String(32), default="static_profile", nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoutingPolicyActivation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "routing_policy_activations"
    __table_args__ = (
        CheckConstraint(
            f"action IN ({_sql_values(POLICY_ACTIVATION_ACTIONS)})",
            name="routing_policy_activation_action",
        ),
    )

    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("routing_policy_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    previous_policy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("routing_policy_versions.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RoutingDecisionSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "routing_decision_snapshots"
    __table_args__ = (
        CheckConstraint(
            f"task_category IN ({_sql_values(TASK_CATEGORIES)})",
            name="routing_decision_task_category",
        ),
        CheckConstraint(
            f"difficulty IN ({_sql_values(TASK_DIFFICULTIES)})",
            name="routing_decision_difficulty",
        ),
        CheckConstraint(
            "classification_confidence >= 0 AND classification_confidence <= 1",
            name="routing_decision_confidence_range",
        ),
    )

    request_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    routing_policy_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("routing_policy_versions.id", ondelete="SET NULL"), index=True
    )
    task_category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    difficulty: Mapped[str] = mapped_column(String(32), nullable=False)
    classification_confidence: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    classifier_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_model: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
