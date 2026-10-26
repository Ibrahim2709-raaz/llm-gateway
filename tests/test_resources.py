import pytest

from app.core.config import Settings
from app.core.resources import create_resources


@pytest.mark.anyio
async def test_create_resources_enables_configured_real_providers() -> None:
    resources = create_resources(
        Settings(
            _env_file=None,
            app_env="test",
            openai_enabled=True,
            openai_api_key="openai-test-key",
            anthropic_enabled=True,
            anthropic_api_key="anthropic-test-key",
            local_enabled=True,
        )
    )
    try:
        assert [provider.name for provider in resources.providers] == [
            "openai",
            "anthropic",
            "local",
            "mock",
        ]
        assert resources.openai_client is not None
        assert resources.anthropic_client is not None
        assert resources.local_client is not None
    finally:
        if resources.openai_client is not None:
            await resources.openai_client.close()
        if resources.anthropic_client is not None:
            await resources.anthropic_client.close()
        if resources.local_client is not None:
            await resources.local_client.close()
        await resources.redis.aclose()
        await resources.database_engine.dispose()
