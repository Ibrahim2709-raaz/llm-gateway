from pydantic import BaseModel, ConfigDict, Field


class UsageSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: str
    request_count: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    budget_usd: float | None = Field(default=None, ge=0)
    remaining_budget_usd: float | None = Field(default=None, ge=0)
