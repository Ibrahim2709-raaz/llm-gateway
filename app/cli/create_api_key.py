import argparse
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.api_keys import CreatedClientApiKey, create_client_api_key
from app.core.config import get_settings
from app.db.session import session_factory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a gateway client and API key.")
    parser.add_argument("--client-name", required=True)
    parser.add_argument("--key-name", default="default")
    return parser


async def create_key(client_name: str, key_name: str) -> CreatedClientApiKey:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = session_factory(engine)
    try:
        async with factory.begin() as session:
            created = await create_client_api_key(
                session,
                client_name=client_name,
                key_name=key_name,
                pepper=settings.gateway_api_key_pepper.get_secret_value(),
            )
        return created
    finally:
        await engine.dispose()


def main() -> None:
    arguments = build_parser().parse_args()
    created = asyncio.run(create_key(arguments.client_name, arguments.key_name))
    print(f"Client ID: {created.client_id}")
    print(f"API key ID: {created.api_key_id}")
    print("Gateway API key (shown once):")
    print(created.token)


if __name__ == "__main__":
    main()
