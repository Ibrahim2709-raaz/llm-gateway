import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_dependency_settings_have_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.redis_url == "redis://localhost:6379/0"


def test_dependency_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, dependency_check_timeout_seconds=0)


def test_openai_requires_a_nonempty_key_when_enabled() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_enabled=True, openai_api_key="")


def test_openai_can_be_enabled_with_a_secret_key() -> None:
    settings = Settings(_env_file=None, openai_enabled=True, openai_api_key="test-key")

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-key"
    assert "test-key" not in repr(settings)
