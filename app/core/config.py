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
    app_version: str = "0.3.0"
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
    portal_email_rate_limit: int = Field(default=5, ge=1, le=1_000)
    portal_email_rate_limit_window_seconds: int = Field(default=900, ge=60, le=86_400)
    response_cache_enabled: bool = True
    response_cache_ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    intelligent_routing_enabled: bool = False
    intelligent_routing_shadow_enabled: bool = False
    intelligent_routing_canary_percent: int = Field(default=100, ge=0, le=100)
    intelligent_routing_customer_allowlist: str = ""
    intelligent_routing_required_policy_version: str | None = None
    intelligent_routing_required_score_version: str | None = None
    max_request_body_bytes: int = Field(default=1_048_576, ge=1_024, le=52_428_800)
    trusted_hosts: str = "*"
    gateway_api_key_pepper: SecretStr = SecretStr(DEFAULT_DEVELOPMENT_API_KEY_PEPPER)
    browser_session_ttl_seconds: int = Field(default=604_800, ge=300, le=2_592_000)
    browser_session_touch_interval_seconds: int = Field(default=300, ge=30, le=86_400)
    email_verification_ttl_seconds: int = Field(default=86_400, ge=300, le=604_800)
    password_reset_ttl_seconds: int = Field(default=3_600, ge=300, le=86_400)
    organization_invitation_ttl_seconds: int = Field(default=604_800, ge=3_600, le=2_592_000)
    portal_action_token_delivery: Literal["response", "smtp", "disabled"] = "response"
    portal_public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    grafana_public_url: AnyHttpUrl = AnyHttpUrl(
        "http://localhost:3000/d/llm-gateway-overview/llm-gateway-overview"
    )
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = Field(default="LLM Gateway", min_length=1, max_length=200)
    smtp_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    openai_enabled: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="gpt-5.6-terra", min_length=1)
    openai_context_window_tokens: int | None = Field(default=None, ge=1)
    openai_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    openai_input_cost_per_million_usd: float = Field(default=2.0, ge=0)
    openai_output_cost_per_million_usd: float = Field(default=12.0, ge=0)
    openai_estimated_latency_ms: int = Field(default=1500, ge=1)
    openai_quality_score: float = Field(default=0.9, ge=0, le=1)
    anthropic_enabled: bool = False
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = Field(default="claude-sonnet-5-5", min_length=1)
    anthropic_context_window_tokens: int | None = Field(default=None, ge=1)
    anthropic_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    anthropic_input_cost_per_million_usd: float = Field(default=2.0, ge=0)
    anthropic_output_cost_per_million_usd: float = Field(default=10.0, ge=0)
    anthropic_estimated_latency_ms: int = Field(default=1800, ge=1)
    anthropic_quality_score: float = Field(default=0.92, ge=0, le=1)
    local_enabled: bool = False
    local_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:11434/v1")
    local_api_key: SecretStr = SecretStr("ollama")
    local_model: str = Field(default="qwen3:8b", min_length=1)
    local_context_window_tokens: int | None = Field(default=None, ge=1)
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
            self.anthropic_api_key is None or not self.anthropic_api_key.get_secret_value().strip()
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
        controlled_rollout = self.intelligent_routing_canary_percent < 100 or bool(
            self.intelligent_routing_customer_allowlist.strip()
        )
        if (
            self.intelligent_routing_enabled
            and controlled_rollout
            and (
                not self.intelligent_routing_required_policy_version
                or not self.intelligent_routing_required_score_version
            )
        ):
            raise ValueError(
                "Controlled intelligent routing requires pinned policy and score versions"
            )
        if (
            self.app_env == "production"
            and self.gateway_api_key_pepper.get_secret_value() == DEFAULT_DEVELOPMENT_API_KEY_PEPPER
        ):
            raise ValueError("GATEWAY_API_KEY_PEPPER must be changed in production")
        if self.app_env == "production" and self.portal_action_token_delivery == "response":
            raise ValueError("PORTAL_ACTION_TOKEN_DELIVERY=response is forbidden in production")
        if self.portal_action_token_delivery == "smtp":
            if not self.smtp_host or not self.smtp_host.strip():
                raise ValueError("SMTP_HOST is required when SMTP delivery is enabled")
            if not self.smtp_from_email or not self.smtp_from_email.strip():
                raise ValueError("SMTP_FROM_EMAIL is required when SMTP delivery is enabled")
            username_configured = bool(self.smtp_username and self.smtp_username.strip())
            password_configured = bool(self.smtp_password and self.smtp_password.get_secret_value())
            if username_configured != password_configured:
                raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be configured together")
            if not self.smtp_from_name.strip():
                raise ValueError("SMTP_FROM_NAME must not be blank")
            if any(
                character in f"{self.smtp_from_name}{self.smtp_from_email}" for character in "\r\n"
            ):
                raise ValueError("SMTP sender fields cannot contain line breaks")
            assert self.smtp_from_email is not None
            local_part, separator, domain = self.smtp_from_email.strip().partition("@")
            if (
                not separator
                or not local_part
                or not domain
                or "@" in domain
                or any(character.isspace() for character in self.smtp_from_email)
            ):
                raise ValueError("SMTP_FROM_EMAIL must be a valid email address")
            if self.portal_public_base_url.query or self.portal_public_base_url.fragment:
                raise ValueError("PORTAL_PUBLIC_BASE_URL cannot contain a query or fragment")
        if (
            self.app_env == "production"
            and self.portal_action_token_delivery == "smtp"
            and self.portal_public_base_url.scheme != "https"
        ):
            raise ValueError("PORTAL_PUBLIC_BASE_URL must use HTTPS in production")
        if (
            self.app_env == "production"
            and self.portal_action_token_delivery == "smtp"
            and self.smtp_security == "none"
        ):
            raise ValueError("SMTP_SECURITY=none is forbidden in production")
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

    @field_validator(
        "intelligent_routing_required_policy_version",
        "intelligent_routing_required_score_version",
        mode="before",
    )
    @classmethod
    def normalize_optional_routing_version(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator(
        "openai_context_window_tokens",
        "anthropic_context_window_tokens",
        "local_context_window_tokens",
        mode="before",
    )
    @classmethod
    def normalize_optional_context_window(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
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
