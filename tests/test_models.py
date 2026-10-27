from app.db.base import Base
from app.db.models import (
    ApiKey,
    BudgetLimit,
    Client,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRun,
    ModelCapabilityScore,
    ProviderConfig,
    RoutingDecisionSnapshot,
    RoutingPolicyActivation,
    RoutingPolicyVersion,
    UsageRecord,
)


def test_initial_database_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        ApiKey.__tablename__,
        BudgetLimit.__tablename__,
        Client.__tablename__,
        EvaluationCase.__tablename__,
        EvaluationDataset.__tablename__,
        EvaluationRun.__tablename__,
        ModelCapabilityScore.__tablename__,
        ProviderConfig.__tablename__,
        RoutingDecisionSnapshot.__tablename__,
        RoutingPolicyActivation.__tablename__,
        RoutingPolicyVersion.__tablename__,
        UsageRecord.__tablename__,
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
