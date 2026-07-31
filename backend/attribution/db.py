"""Async SQLAlchemy lifecycle and optional PostgreSQL Entra token wiring."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .models import Base

POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

#: How long a SQLite connection waits for a lock before raising "database is locked".
#: Kept well under the frontend request timeout so contention surfaces as a real error
#: rather than as a client-side timeout.
SQLITE_BUSY_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class DatabaseSettings:
    url: str = os.getenv("ATTRIBUTION_DATABASE_URL", "sqlite+aiosqlite:///./attribution.db")
    entra_auth: bool = os.getenv("ATTRIBUTION_POSTGRES_ENTRA_AUTH", "").lower() == "true"
    managed_identity_client_id: str | None = (
        os.getenv("ATTRIBUTION_POSTGRES_MANAGED_IDENTITY_CLIENT_ID")
        or os.getenv("AZURE_CLIENT_ID")
    )

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        return cls(
            url=os.getenv("ATTRIBUTION_DATABASE_URL", "sqlite+aiosqlite:///./attribution.db"),
            entra_auth=os.getenv("ATTRIBUTION_POSTGRES_ENTRA_AUTH", "").lower() == "true",
            managed_identity_client_id=(
                os.getenv("ATTRIBUTION_POSTGRES_MANAGED_IDENTITY_CLIENT_ID")
                or os.getenv("AZURE_CLIENT_ID")
                or None
            ),
        )


class Database:
    """Owns the engine/sessionmaker; migrations are run separately in production."""

    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self.settings = settings or DatabaseSettings.from_env()
        kwargs: dict = {"pool_pre_ping": True}
        if self.settings.url.startswith("sqlite+aiosqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        self.engine: AsyncEngine = create_async_engine(self.settings.url, **kwargs)
        if self.settings.url.startswith("sqlite"):
            self._configure_sqlite_pragmas()
        if self.settings.entra_auth:
            self._configure_entra_password()
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    def _configure_entra_password(self) -> None:
        """Inject a fresh Entra token for asyncpg connections without storing it."""
        if not self.settings.url.startswith("postgresql+asyncpg"):
            raise ValueError("ATTRIBUTION_POSTGRES_ENTRA_AUTH requires postgresql+asyncpg")
        try:
            from azure.identity import ManagedIdentityCredential
        except ImportError as exc:  # pragma: no cover - deployment configuration
            raise RuntimeError("azure-identity is required for PostgreSQL Entra auth") from exc

        credential = ManagedIdentityCredential(client_id=self.settings.managed_identity_client_id)

        @event.listens_for(self.engine.sync_engine, "do_connect")
        def _set_token(_dialect, _connection_record, cargs, cparams):
            cparams["password"] = credential.get_token(POSTGRES_SCOPE).token

    def _configure_sqlite_pragmas(self) -> None:
        """Enable foreign keys, and let API reads run while the worker writes.

        SQLite's default rollback journal gives a writer an exclusive lock over the
        whole database, so every API read blocks until the attribution worker's write
        transaction commits. With the default five second busy timeout that stalls
        reads for longer than the browser is willing to wait, which surfaces as
        "attribution service unavailable" even though the backend is healthy. WAL lets
        readers proceed against the last committed snapshot instead of blocking.
        """
        in_memory = ":memory:" in self.settings.url

        @event.listens_for(self.engine.sync_engine, "connect")
        def _apply_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            if not in_memory:
                # WAL is a persistent database property; re-applying is a cheap no-op.
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            cursor.close()

    async def init_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session


def postgres_url(host: str, database: str, user: str, port: int = 5432) -> str:
    """Build a passwordless asyncpg URL for Managed Identity/Entra deployments."""
    return str(URL.create("postgresql+asyncpg", username=user, host=host, port=port, database=database))
