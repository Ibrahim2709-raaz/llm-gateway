from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEVELOPMENT_API_KEY_PEPPER = "development-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "LLM Gateway"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    redis_url: str = "redis://localhost:6379/0"
    dependency_check_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    request_rate_limit: int = Field(default=60, ge=1, le=1_000_000)
    request_rate_limit_window_seconds: int = Field(default=60, ge=1, le=86_400)
    response_cache_enabled: bool = True
    response_cache_ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    intelligent_routing_enabled: bool = False
    intelligent_routing_shadow_enabled: bool = False
    intelligent_routing_canary_percent: int = Field(default=100, ge=0, le=100)
    intelligent_routing_customer_allowlist: str = ""
    max_request_body_bytes: int = Field(default=1_048_576, ge=1_024, le=52_428_800)
    trusted_hosts: str = "*"
    gateway_api_key_pepper: SecretStr = SecretStr(DEFAULT_DEVELOPMENT_API_KEY_PEPPER)
    openai_enabled: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="gpt-5.6-terra", min_length=1)
    openai_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    openai_input_cost_per_million_usd: float = Field(default=2.0, ge=0)
    openai_output_cost_per_million_usd: float = Field(default=12.0, ge=0)
    openai_estimated_latency_ms: int = Field(default=1500, ge=1)
    openai_quality_score: float = Field(default=0.9, ge=0, le=1)
    anthropic_enabled: bool = False
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = Field(default="claude-sonnet-5-5", min_length=1)
    anthropic_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    anthropic_input_cost_per_million_usd: float = Field(default=2.0, ge=0)
    anthropic_output_cost_per_million_usd: float = Field(default=10.0, ge=0)
    anthropic_estimated_latency_ms: int = Field(default=1800, ge=1)
    anthropic_quality_score: float = Field(default=0.92, ge=0, le=1)
    local_enabled: bool = False
    local_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:11434/v1")
    local_api_key: SecretStr = SecretStr("ollama")
    local_model: str = Field(default="qwen3:8b", min_length=1)
    local_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    local_input_cost_per_million_usd: float = Field(default=0.0, ge=0)
    local_output_cost_per_million_usd: float = Field(default=0.0, ge=0)
    local_estimated_latency_ms: int = Field(default=2500, ge=1)
    local_quality_score: float = Field(default=0.7, ge=0, le=1)
    provider_max_retries: int = Field(default=2, ge=0, le=10)
    provider_retry_base_delay_seconds: float = Field(default=0.25, ge=0, le=30)
    provider_retry_max_delay_seconds: float = Field(default=2.0, ge=0, le=60)
    provider_circuit_breaker_failure_threshold: int = Field(default=5, ge=1, le=100)
    provider_circuit_breaker_recovery_seconds: float = Field(default=30.0, gt=0, le=3600)

    @model_validator(mode="after")
    def enabled_openai_requires_key(self) -> "Settings":
        missing_key = (
            self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip()
        )
        if self.openai_enabled and missing_key:
            raise ValueError("OPENAI_API_KEY is required when OPENAI_ENABLED is true")
        missing_anthropic_key = (
            self.anthropic_api_key is None
            or not self.anthropic_api_key.get_secret_value().strip()
        )
        if self.anthropic_enabled and missing_anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY is required when ANTHROPIC_ENABLED is true")
        if self.local_enabled and not self.local_api_key.get_secret_value().strip():
            raise ValueError("LOCAL_API_KEY must not be empty when LOCAL_ENABLED is true")
        if self.provider_retry_max_delay_seconds < self.provider_retry_base_delay_seconds:
            raise ValueError(
                "PROVIDER_RETRY_MAX_DELAY_SECONDS must be greater than or equal to "
                "PROVIDER_RETRY_BASE_DELAY_SECONDS"
            )
        if self.intelligent_routing_enabled and self.intelligent_routing_shadow_enabled:
            raise ValueError(
                "INTELLIGENT_ROUTING_ENABLED and INTELLIGENT_ROUTING_SHADOW_ENABLED "
                "cannot both be true"
            )
        if (
            self.app_env == "production"
            and self.gateway_api_key_pepper.get_secret_value() == DEFAULT_DEVELOPMENT_API_KEY_PEPPER
        ):
            raise ValueError("GATEWAY_API_KEY_PEPPER must be changed in production")
        if not self.trusted_host_list:
            raise ValueError("TRUSTED_HOSTS must contain at least one host")
        if self.app_env == "production" and "*" in self.trusted_host_list:
            raise ValueError("TRUSTED_HOSTS must not contain '*' in production")
        return self

    @field_validator("intelligent_routing_customer_allowlist")
    @classmethod
    def validate_intelligent_routing_customer_allowlist(cls, value: str) -> str:
        for item in value.split(","):
            if item.strip():
                UUID(item.strip())
        return value

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]

    @property
    def intelligent_routing_customer_ids(self) -> frozenset[UUID]:
        return frozenset(
            UUID(item.strip())
            for item in self.intelligent_routing_customer_allowlist.split(",")
            if item.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
