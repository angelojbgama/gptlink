"""Database lifecycle and a shared transaction boundary for repositories."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gptlink.persistence.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine.sync_engine, "connect", self._configure_sqlite)

    @staticmethod
    def _configure_sqlite(connection, connection_record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    async def init(self) -> None:
        """Create missing tables, retaining existing data."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Commit related repository operations together, or roll everything back."""
        async with self._sessions.begin() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()

    async def check(self) -> None:
        """Probe an actual DB round-trip, including when no devices are online."""
        async with self.transaction() as session:
            await session.execute(select(1))
