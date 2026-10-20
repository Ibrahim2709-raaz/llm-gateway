from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from fastapi import status

from app.core.errors import GatewayError
from app.execution.service import (
    ProviderExecutionExhausted,
    ProviderExecutor,
    Sleeper,
)
from app.providers.base import ChatProvider, ProviderRequestError
from app.routing.service import ProviderRouter
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    GatewayMetadata,
    ResponseMessage,
    TokenUsage,
)

logger = structlog.get_logger(__name__)


class ChatCompletionService:
    def __init__(
        self,
        providers: tuple[ChatProvider, ...],
        *,
        max_retries: int = 0,
        retry_base_delay_seconds: float = 0,
        retry_max_delay_seconds: float = 0,
        sleeper: Sleeper | None = None,
    ) -> None:
        self.router = ProviderRouter(providers)
        self.executor = ProviderExecutor(
            max_retries=max_retries,
            retry_base_delay_seconds=retry_base_delay_seconds,
            retry_max_delay_seconds=retry_max_delay_seconds,
            sleeper=sleeper,
        )

    async def create(
        self, request: ChatCompletionRequest, request_id: UUID
    ) -> ChatCompletionResponse:
        routing_plan = self.router.plan(request)
        primary = routing_plan.candidates[0]
        logger.info(
            "provider_route_planned",
            request_id=str(request_id),
            provider=primary.provider.name,
            model=primary.provider.model,
            strategy="explicit" if request.model != "auto" else request.routing.strategy,
            task_complexity=routing_plan.task_complexity,
            estimated_cost_usd=str(primary.estimated_cost_usd),
            candidate_count=len(routing_plan.candidates),
        )
        try:
            execution = await self.executor.execute(routing_plan, request, request_id)
        except ProviderRequestError as exc:
            raise GatewayError(
                str(exc),
                code="invalid_request",
                error_type="invalid_request_error",
                status_code=status.HTTP_400_BAD_REQUEST,
                param="max_tokens",
            ) from exc
        except ProviderExecutionExhausted as exc:
            if exc.all_attempts_timed_out:
                raise GatewayError(
                    "All eligible provider attempts timed out.",
                    code="provider_timeout",
                    error_type="provider_error",
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                ) from exc
            raise GatewayError(
                "All eligible provider attempts failed.",
                code="provider_error",
                error_type="provider_error",
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from exc
        provider = execution.candidate.provider
        result = execution.result
        total_tokens = result.prompt_tokens + result.completion_tokens
        routing_strategy = "explicit" if request.model != "auto" else request.routing.strategy

        return ChatCompletionResponse(
            id=f"chatcmpl_{uuid4().hex}",
            created=int(datetime.now(UTC).timestamp()),
            model=result.model,
            choices=[
                Choice(
                    index=0,
                    message=ResponseMessage(content=result.content),
                    finish_reason=result.finish_reason,
                )
            ],
            usage=TokenUsage(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=total_tokens,
            ),
            gateway=GatewayMetadata(
                request_id=request_id,
                provider=provider.name,
                cost_usd=float(result.cost_usd),
                cached=False,
                routing_strategy=routing_strategy,
                task_complexity=routing_plan.task_complexity,
                estimated_cost_usd=float(execution.candidate.estimated_cost_usd),
                attempts=list(execution.attempts),
            ),
        )
