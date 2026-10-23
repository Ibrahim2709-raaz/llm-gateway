import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_dependency_settings_have_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.redis_url == "redis://localhost:6379/0"
    assert "development-only-change-me" not in repr(settings)


def test_dependency_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, dependency_check_timeout_seconds=0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_rate_limit", 0),
        ("request_rate_limit_window_seconds", 0),
        ("response_cache_ttl_seconds", 0),
        ("provider_circuit_breaker_failure_threshold", 0),
        ("provider_circuit_breaker_recovery_seconds", 0),
    ],
)
def test_positive_runtime_settings(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_retry_max_delay_cannot_be_lower_than_base_delay() -> None:
    with pytest.raises(ValidationError, match="PROVIDER_RETRY_MAX_DELAY_SECONDS"):
        Settings(
            _env_file=None,
            provider_retry_base_delay_seconds=2,
            provider_retry_max_delay_seconds=1,
        )


def test_openai_requires_a_nonempty_key_when_enabled() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_enabled=True, openai_api_key="")


def test_openai_can_be_enabled_with_a_secret_key() -> None:
    settings = Settings(_env_file=None, openai_enabled=True, openai_api_key="test-key")

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-key"
    assert "test-key" not in repr(settings)


def test_production_requires_a_custom_api_key_pepper() -> None:
    with pytest.raises(ValidationError, match="GATEWAY_API_KEY_PEPPER"):
        Settings(_env_file=None, app_env="production")


def test_production_accepts_a_custom_api_key_pepper() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        gateway_api_key_pepper="a-long-production-secret",
        trusted_hosts="api.example.com,gateway",
    )

    assert settings.gateway_api_key_pepper.get_secret_value() == "a-long-production-secret"
    assert "a-long-production-secret" not in repr(settings)
    assert settings.trusted_host_list == ["api.example.com", "gateway"]
