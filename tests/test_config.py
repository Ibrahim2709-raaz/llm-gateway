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
