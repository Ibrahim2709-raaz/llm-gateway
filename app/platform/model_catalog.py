from dataclasses import dataclass

from app.core.config import Settings
from app.providers.base import TaskComplexity


@dataclass(frozen=True, slots=True)
class CatalogModel:
    provider: str
    model: str
    enabled: bool
    input_cost_per_million_usd: float
    output_cost_per_million_usd: float
    context_window_tokens: int | None
    estimated_latency_ms: int
    quality_score: float
    max_complexity: TaskComplexity = "complex"
    auto_selectable: bool = True


def build_model_catalog(
    settings: Settings, *, available_provider_names: frozenset[str]
) -> tuple[CatalogModel, ...]:
    """Return configured model metadata without ever reading provider credentials."""
    configured = (
        CatalogModel(
            provider="openai",
            model=settings.openai_model,
            enabled=settings.openai_enabled and "openai" in available_provider_names,
            input_cost_per_million_usd=settings.openai_input_cost_per_million_usd,
            output_cost_per_million_usd=settings.openai_output_cost_per_million_usd,
            context_window_tokens=settings.openai_context_window_tokens,
            estimated_latency_ms=settings.openai_estimated_latency_ms,
            quality_score=settings.openai_quality_score,
        ),
        CatalogModel(
            provider="anthropic",
            model=settings.anthropic_model,
            enabled=settings.anthropic_enabled and "anthropic" in available_provider_names,
            input_cost_per_million_usd=settings.anthropic_input_cost_per_million_usd,
            output_cost_per_million_usd=settings.anthropic_output_cost_per_million_usd,
            context_window_tokens=settings.anthropic_context_window_tokens,
            estimated_latency_ms=settings.anthropic_estimated_latency_ms,
            quality_score=settings.anthropic_quality_score,
        ),
        CatalogModel(
            provider="local",
            model=settings.local_model,
            enabled=settings.local_enabled and "local" in available_provider_names,
            input_cost_per_million_usd=settings.local_input_cost_per_million_usd,
            output_cost_per_million_usd=settings.local_output_cost_per_million_usd,
            context_window_tokens=settings.local_context_window_tokens,
            estimated_latency_ms=settings.local_estimated_latency_ms,
            quality_score=settings.local_quality_score,
        ),
        CatalogModel(
            provider="mock",
            model="mock-1",
            enabled="mock" in available_provider_names,
            input_cost_per_million_usd=0,
            output_cost_per_million_usd=0,
            context_window_tokens=None,
            estimated_latency_ms=5,
            quality_score=0.1,
            auto_selectable=False,
        ),
    )
    return configured
