from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PortalUsageSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpdateBudgetRequest(PortalUsageSchema):
    limit_usd: Decimal = Field(gt=0, max_digits=18, decimal_places=8)
    alert_threshold_percent: int = Field(default=80, ge=1, le=100)


class BudgetResponse(PortalUsageSchema):
    period: str
    limit_usd: Decimal
    alert_threshold_percent: int
