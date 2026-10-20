from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    gateway_api_key_pepper: SecretStr = SecretStr(DEFAULT_DEVELOPMENT_API_KEY_PEPPER)
    openai_enabled: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="gpt-5.6-terra", min_length=1)
    openai_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    openai_input_cost_per_million_usd: float = Field(default=2.0, ge=0)
    openai_output_cost_per_million_usd: float = Field(default=12.0, ge=0)
    openai_estimated_latency_ms: int = Field(default=1500, ge=1)
    openai_quality_score: float = Field(default=0.9, ge=0, le=1)
    provider_max_retries: int = Field(default=2, ge=0, le=10)
    provider_retry_base_delay_seconds: float = Field(default=0.25, ge=0, le=30)
    provider_retry_max_delay_seconds: float = Field(default=2.0, ge=0, le=60)

    @model_validator(mode="after")
    def enabled_openai_requires_key(self) -> "Settings":
        missing_key = (
            self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip()
        )
        if self.openai_enabled and missing_key:
            raise ValueError("OPENAI_API_KEY is required when OPENAI_ENABLED is true")
        if self.provider_retry_max_delay_seconds < self.provider_retry_base_delay_seconds:
            raise ValueError(
                "PROVIDER_RETRY_MAX_DELAY_SECONDS must be greater than or equal to "
                "PROVIDER_RETRY_BASE_DELAY_SECONDS"
            )
        if (
            self.app_env == "production"
            and self.gateway_api_key_pepper.get_secret_value() == DEFAULT_DEVELOPMENT_API_KEY_PEPPER
        ):
            raise ValueError("GATEWAY_API_KEY_PEPPER must be changed in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
