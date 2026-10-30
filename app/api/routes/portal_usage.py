from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import GatewayError
from app.db.session import get_database_session
from app.identity.audit import append_audit_event
from app.identity.authorization import (
    BUDGETS_MANAGE,
    USAGE_READ,
    AuthorizationError,
    require_organization_permission,
)
from app.identity.dependencies import (
    BrowserIdentity,
    authenticate_portal_session,
    require_portal_csrf,
)
from app.schemas.portal_usage import BudgetResponse, UpdateBudgetRequest
from app.schemas.usage import UsageSummaryResponse
from app.usage.service import DatabaseUsageService, set_budget_limit

router = APIRouter(prefix="/v1/portal/organizations/{organization_id}", tags=["Portal usage"])


def _raise_authorization_error(error: AuthorizationError) -> NoReturn:
    if error.code == "cross_organization_access":
        raise GatewayError(
            "The organization is unavailable.",
            code="organization_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    raise GatewayError(
        "Permission to access organization usage is required.",
        code=error.code,
        error_type="authorization_error",
        status_code=status.HTTP_403_FORBIDDEN,
    ) from error


@router.get("/usage", response_model=UsageSummaryResponse, operation_id="getPortalUsageSummary")
async def get_portal_usage_summary(
    organization_id: UUID,
    period: Annotated[str, Query(pattern=r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$")],
    identity: Annotated[BrowserIdentity, Depends(authenticate_portal_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> UsageSummaryResponse:
    try:
        require_organization_permission(
            identity.principal,
            organization_id=organization_id,
            permission=USAGE_READ,
        )
    except AuthorizationError as exc:
        _raise_authorization_error(exc)
    summary = await DatabaseUsageService(database).summary(identity.organization.client_id, period)
    return UsageSummaryResponse(
        period=summary.period,
        request_count=summary.request_count,
        prompt_tokens=summary.prompt_tokens,
        completion_tokens=summary.completion_tokens,
        total_tokens=summary.total_tokens,
        cost_usd=float(summary.cost_usd),
        budget_usd=float(summary.budget_usd) if summary.budget_usd is not None else None,
        remaining_budget_usd=(
            float(summary.remaining_budget_usd)
            if summary.remaining_budget_usd is not None
            else None
        ),
    )


@router.put(
    "/budgets/{period}",
    response_model=BudgetResponse,
    operation_id="updatePortalBudget",
)
async def update_portal_budget(
    organization_id: UUID,
    period: str,
    payload: UpdateBudgetRequest,
    identity: Annotated[BrowserIdentity, Depends(require_portal_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> BudgetResponse:
    try:
        require_organization_permission(
            identity.principal,
            organization_id=organization_id,
            permission=BUDGETS_MANAGE,
        )
        budget = await set_budget_limit(
            database,
            client_id=identity.organization.client_id,
            period=period,
            limit_usd=payload.limit_usd,
            alert_threshold_percent=payload.alert_threshold_percent,
        )
        await database.flush()
        await append_audit_event(
            database,
            principal=identity.principal,
            action="budget.updated",
            resource_type="budget_limit",
            organization_id=organization_id,
            resource_id=str(budget.id),
            event_data={
                "period": period,
                "limit_usd": str(budget.limit_usd),
                "alert_threshold_percent": budget.alert_threshold_percent,
            },
        )
        await database.commit()
    except AuthorizationError as exc:
        await database.rollback()
        _raise_authorization_error(exc)
    except ValueError as exc:
        await database.rollback()
        raise GatewayError(
            "The budget configuration is invalid.",
            code="invalid_budget",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    return BudgetResponse(
        period=budget.period,
        limit_usd=budget.limit_usd,
        alert_threshold_percent=budget.alert_threshold_percent,
    )
