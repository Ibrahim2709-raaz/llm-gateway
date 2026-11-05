from pydantic import BaseModel, ConfigDict, Field


class PortalPlaygroundSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlaygroundPreviewResponse(PortalPlaygroundSchema):
    selected_provider: str
    selected_model: str
    routing_strategy: str
    estimated_cost_usd: float = Field(ge=0)
    task_category: str
    difficulty: str
    classification_confidence: float = Field(ge=0, le=1)
    policy_version: str
    score_version: str
    reason_codes: list[str]
    eligible_candidate_count: int = Field(ge=1)
