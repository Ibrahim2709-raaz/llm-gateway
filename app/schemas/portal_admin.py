from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class AdminSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelCatalogEntry(AdminSchema):
    provider: str
    model: str
    availability: Literal["available", "disabled"]
    input_cost_per_million_usd: float = Field(ge=0)
    output_cost_per_million_usd: float = Field(ge=0)
    context_window_tokens: int | None = Field(default=None, ge=1)
    capabilities: list[str]
    estimated_latency_ms: int = Field(ge=1)
    quality_score: float = Field(ge=0, le=1)
    max_complexity: Literal["simple", "medium", "complex"]
    auto_selectable: bool
    configuration_source: Literal["environment"] = "environment"
    pricing_source: Literal["configured_estimate"] = "configured_estimate"


class ModelCatalogResponse(AdminSchema):
    data: list[ModelCatalogEntry]
    credentials_exposed: bool = False


class CreatePricingProposalRequest(AdminSchema):
    provider: Literal["openai", "anthropic", "local"]
    model: str = Field(min_length=1, max_length=128)
    input_cost_per_million_usd: Decimal = Field(ge=0, max_digits=14, decimal_places=6)
    output_cost_per_million_usd: Decimal = Field(ge=0, max_digits=14, decimal_places=6)
    source_url: AnyHttpUrl
    rationale: str = Field(min_length=10, max_length=1_000)


class ReviewPricingProposalRequest(AdminSchema):
    decision: Literal["approved", "rejected"]
    review_note: str = Field(min_length=3, max_length=1_000)


class PricingProposalResponse(AdminSchema):
    id: UUID
    provider: str
    model: str
    input_cost_per_million_usd: Decimal
    output_cost_per_million_usd: Decimal
    source_url: str
    rationale: str
    status: Literal["pending", "approved", "rejected"]
    proposed_by_user_id: UUID
    reviewed_by_user_id: UUID | None
    review_note: str | None
    created_at: datetime
    reviewed_at: datetime | None


class PricingProposalListResponse(AdminSchema):
    data: list[PricingProposalResponse]


class EvaluationRunResponse(AdminSchema):
    id: UUID
    dataset_name: str
    dataset_version: str
    provider: str
    model: str
    status: Literal["pending", "running", "completed", "failed"]
    runner_version: str
    case_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)
    average_score: float | None = Field(default=None, ge=0, le=1)
    total_cost_usd: Decimal = Field(ge=0)
    started_at: datetime | None
    completed_at: datetime | None


class EvaluationRunListResponse(AdminSchema):
    data: list[EvaluationRunResponse]


class EvaluationDatasetResponse(AdminSchema):
    name: str
    version: str
    description: str
    case_count: int = Field(ge=0)


class EvaluationDatasetListResponse(AdminSchema):
    data: list[EvaluationDatasetResponse]


class EvaluationPreflightRequest(AdminSchema):
    dataset_name: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)


class EvaluationPreflightResponse(AdminSchema):
    dataset_name: str
    dataset_version: str
    provider: str
    model: str
    case_count: int = Field(ge=1)
    estimated_input_tokens: int = Field(ge=1)
    maximum_output_tokens: int = Field(ge=1)
    estimated_max_cost_usd: Decimal = Field(ge=0)
    paid_provider: bool


class CreateEvaluationRunRequest(EvaluationPreflightRequest):
    approved_max_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=8)
    confirmation: Literal["CREATE_EVALUATION"]


class ExecuteEvaluationRunRequest(AdminSchema):
    confirmation: Literal["RUN_EVALUATION"]


class EvaluationExecutionResponse(AdminSchema):
    run_id: UUID
    status: Literal["completed"]
    completed_cases: int = Field(ge=0)
    skipped_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    average_score: float | None = Field(default=None, ge=0, le=1)
    total_cost_usd: Decimal = Field(ge=0)


class PublishEvaluationScoresRequest(AdminSchema):
    score_version: str = Field(min_length=1, max_length=64)


class PublishEvaluationScoresResponse(AdminSchema):
    score_version: str
    score_count: int = Field(ge=1)
    sample_count: int = Field(ge=1)


class EvaluationFailureResponse(AdminSchema):
    result_id: UUID
    case_external_id: str
    task_category: str
    difficulty: str
    outcome: Literal["completed", "failed", "timed_out", "refused"]
    score: Decimal | None
    error_code: str | None
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=0)
    latency_ms: int = Field(ge=0)


class EvaluationFailureListResponse(AdminSchema):
    data: list[EvaluationFailureResponse]
    prompts_exposed: bool = False
    outputs_exposed: bool = False


class ScoreVersionResponse(AdminSchema):
    provider: str
    model: str
    score_version: str
    status: Literal["draft", "active", "retired"]
    score_count: int = Field(ge=1)
    sample_count: int = Field(ge=1)
    weighted_quality_score: float = Field(ge=0, le=1)
    task_categories: list[str]
    valid_until: datetime | None


class ScoreVersionListResponse(AdminSchema):
    data: list[ScoreVersionResponse]


class ScoreComparisonRowResponse(AdminSchema):
    task_category: str
    difficulty: str
    candidate_quality: Decimal = Field(ge=0, le=1)
    active_quality: Decimal | None = Field(default=None, ge=0, le=1)
    quality_delta: Decimal | None
    candidate_samples: int = Field(ge=1)
    candidate_variance: Decimal | None = Field(default=None, ge=0)
    capabilities: list[str]


class ScoreComparisonResponse(AdminSchema):
    provider: str
    model: str
    candidate_version: str
    candidate_status: Literal["draft", "active", "retired"]
    active_version: str | None
    rows: list[ScoreComparisonRowResponse]


class ChangeScoreActivationRequest(AdminSchema):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    score_version: str = Field(min_length=1, max_length=64)
    action: Literal["activate", "rollback"]
    reason: str = Field(min_length=3, max_length=500)
    valid_days: int = Field(default=30, ge=1, le=365)
    minimum_samples: int = Field(default=5, ge=1)
    confirmation: Literal["CHANGE_SCORE_SET"]


class ScoreActivationResponse(AdminSchema):
    provider: str
    model: str
    score_version: str
    previous_score_version: str | None
    action: Literal["activate", "rollback"]
    score_count: int = Field(ge=1)
    valid_until: datetime
