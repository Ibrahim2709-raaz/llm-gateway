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
