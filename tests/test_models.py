from app.db.base import Base
from app.db.models import (
    ApiKey,
    AuditEvent,
    BrowserSession,
    BudgetLimit,
    Client,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationRun,
    ModelCapabilityScore,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    ProviderConfig,
    RoutingDecisionSnapshot,
    RoutingPolicyActivation,
    RoutingPolicyVersion,
    ScoreSetActivation,
    ServiceAccount,
    UsageRecord,
    User,
    UserActionToken,
)


def test_initial_database_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        ApiKey.__tablename__,
        AuditEvent.__tablename__,
        BrowserSession.__tablename__,
        BudgetLimit.__tablename__,
        Client.__tablename__,
        EvaluationCase.__tablename__,
        EvaluationCaseResult.__tablename__,
        EvaluationDataset.__tablename__,
        EvaluationRun.__tablename__,
        ModelCapabilityScore.__tablename__,
        Organization.__tablename__,
        OrganizationInvitation.__tablename__,
        OrganizationMembership.__tablename__,
        ProviderConfig.__tablename__,
        RoutingDecisionSnapshot.__tablename__,
        RoutingPolicyActivation.__tablename__,
        RoutingPolicyVersion.__tablename__,
        ScoreSetActivation.__tablename__,
        ServiceAccount.__tablename__,
        UsageRecord.__tablename__,
        User.__tablename__,
        UserActionToken.__tablename__,
    }


def test_sensitive_api_key_material_is_not_stored() -> None:
    columns = set(ApiKey.__table__.columns.keys())

    assert "key_hash" in columns
    assert "key_prefix" in columns
    assert "key" not in columns


def test_usage_records_snapshot_provider_identity() -> None:
    columns = set(UsageRecord.__table__.columns.keys())

    assert {"provider", "model", "input_tokens", "output_tokens", "cost_usd"} <= columns


def test_evaluation_cases_do_not_store_production_customer_identity() -> None:
    columns = set(EvaluationCase.__table__.columns.keys())

    assert {"dataset_id", "task_category", "difficulty", "prompt"} <= columns
    assert "client_id" not in columns
    assert "request_id" not in columns


def test_capability_scores_are_versioned_by_task_and_difficulty() -> None:
    columns = set(ModelCapabilityScore.__table__.columns.keys())

    assert {
        "score_version",
        "task_category",
        "difficulty",
        "quality_score",
        "sample_count",
        "capabilities",
        "valid_until",
    } <= columns


def test_evaluation_results_capture_outcomes_usage_and_cost() -> None:
    columns = set(EvaluationCaseResult.__table__.columns.keys())

    assert {
        "evaluation_run_id",
        "evaluation_case_id",
        "outcome",
        "actual_output",
        "score",
        "metrics",
        "prompt_tokens",
        "completion_tokens",
        "cost_usd",
        "latency_ms",
        "error_code",
    } <= columns


def test_routing_snapshots_store_explanations_without_prompt_content() -> None:
    columns = set(RoutingDecisionSnapshot.__table__.columns.keys())

    assert {
        "request_id",
        "task_category",
        "difficulty",
        "classification_confidence",
        "reason_codes",
    } <= columns
    assert "prompt" not in columns
    assert "response" not in columns


def test_policy_activations_are_audited_without_secrets() -> None:
    columns = set(RoutingPolicyActivation.__table__.columns.keys())

    assert {"policy_id", "previous_policy_id", "action", "actor", "reason"} <= columns
    assert "api_key" not in columns


def test_score_activations_are_audited_without_credentials() -> None:
    columns = set(ScoreSetActivation.__table__.columns.keys())

    assert {
        "evaluation_run_id",
        "provider",
        "model",
        "score_version",
        "previous_score_version",
        "action",
        "actor",
        "reason",
    } <= columns
    assert "api_key" not in columns


def test_identity_secrets_are_stored_only_as_hashes() -> None:
    invitation_columns = set(OrganizationInvitation.__table__.columns.keys())
    service_account_columns = set(ServiceAccount.__table__.columns.keys())

    assert "token_hash" in invitation_columns
    assert "token" not in invitation_columns
    assert "secret" not in service_account_columns
    assert "api_key" not in service_account_columns


def test_audit_events_are_append_only_and_prompt_free() -> None:
    columns = set(AuditEvent.__table__.columns.keys())

    assert {
        "organization_id",
        "actor_type",
        "actor_id",
        "action",
        "resource_type",
        "resource_id",
        "request_id",
        "event_data",
        "created_at",
    } <= columns
    assert "updated_at" not in columns
    assert "prompt" not in columns
    assert "response" not in columns


def test_browser_authentication_stores_only_hashed_tokens() -> None:
    for model in (BrowserSession, UserActionToken):
        columns = set(model.__table__.columns.keys())
        assert {"token_prefix", "token_hash", "expires_at"} <= columns
        assert "token" not in columns

    assert "revoked_at" in BrowserSession.__table__.columns
    assert "consumed_at" in UserActionToken.__table__.columns
