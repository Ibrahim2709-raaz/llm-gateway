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
