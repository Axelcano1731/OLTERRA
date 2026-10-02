"""Sesiones de base de datos amarradas a un tenant.

Toda consulta de la aplicación pasa por ``tenant_session``: abre una transacción y
fija ``olterra.tenant_id`` SOLO para esa transacción (``set_config(..., true)``).
Las políticas RLS comparan cada fila con ese valor. Si nadie lo fijó, la función
``olterra_current_tenant()`` devuelve NULL y no se ve ni se escribe nada: el
aislamiento falla cerrado, nunca abierto.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

TENANT_GUC = "olterra.tenant_id"


def create_engine(url: str, **kwargs: object) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True, **kwargs)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def set_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    await session.execute(
        text("SELECT set_config(:guc, :tid, true)"), {"guc": TENANT_GUC, "tid": str(tenant_id)}
    )


@asynccontextmanager
async def tenant_session(
    factory: async_sessionmaker[AsyncSession], tenant_id: UUID
) -> AsyncIterator[AsyncSession]:
    """Transacción con el tenant fijado. Hace commit al salir sin error; si no, rollback."""
    async with factory() as session, session.begin():
        await set_tenant(session, tenant_id)
        yield session
