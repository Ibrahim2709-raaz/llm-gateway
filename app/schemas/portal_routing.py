from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RoutingSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutingDecisionResponse(RoutingSchema):
    request_id: UUID
    task_category: Literal["coding", "reasoning", "summarization", "extraction", "general_chat"]
    difficulty: Literal["simple", "medium", "complex"]
    classification_confidence: Decimal = Field(ge=0, le=1)
    classifier_version: str
    policy_version: str
    score_version: str
    selected_provider: str
    selected_model: str
    reason_codes: list[str]
    candidate_count: int = Field(ge=1)
    created_at: datetime


class RoutingDecisionListResponse(RoutingSchema):
    data: list[RoutingDecisionResponse]


class UpdateRoutingPreferenceRequest(RoutingSchema):
    strategy: Literal["balanced", "cost", "latency", "quality"]


class RoutingPreferenceResponse(RoutingSchema):
    strategy: Literal["balanced", "cost", "latency", "quality"]
