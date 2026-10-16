from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = session_factory(engine)
    async with factory() as session:
        yield session


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    async for session in session_scope(request.app.state.resources.database_engine):
        yield session
