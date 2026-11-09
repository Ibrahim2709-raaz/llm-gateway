from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
