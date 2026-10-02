"""Autenticación por llave de API y contexto de tenant.

La llave se verifica DENTRO de una transacción con el tenant fijado: si la llave
dice ser del tenant A pero su id es de otro, RLS hace que no aparezca y la
respuesta es 401. Cada endpoint abre después sus propias transacciones con
``ctx.session()``, así controla cuándo confirma y nada se confirma después de
haber respondido.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from olterra.api.state import AppState
from olterra.db.models import ApiKey
from olterra.db.session import tenant_session
from olterra.security import apikeys

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Llave de API inválida o revocada",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.olterra
    return state


@dataclass(frozen=True)
class TenantContext:
    tenant_id: UUID
    key_id: UUID
    key_name: str
    actor: str
    scopes: tuple[str, ...]
    client_ip: str | None
    _factory: async_sessionmaker[AsyncSession]

    def session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return tenant_session(self._factory, self.tenant_id)

    def require(self, scope: str) -> None:
        if "*" not in self.scopes and scope not in self.scopes:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"La llave no tiene el permiso '{scope}'"
            )


def _client_ip(request: Request) -> str | None:
    """IP del cliente para la bitácora, o None si el servidor no da una IP (socket Unix, pruebas)."""
    host = request.client.host if request.client else None
    try:
        return str(ip_address(host)) if host else None
    except ValueError:
        return None


async def current_tenant(
    request: Request,
    state: Annotated[AppState, Depends(get_state)],
    authorization: Annotated[str | None, Header()] = None,
) -> TenantContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UNAUTHORIZED
    try:
        parsed = apikeys.parse(authorization[7:])
    except apikeys.InvalidApiKey:
        raise UNAUTHORIZED from None
    async with tenant_session(state.sessions, parsed.tenant_id) as session:
        key = await session.get(ApiKey, parsed.key_id)
        if (
            key is None
            or key.revoked_at is not None
            or not apikeys.verify(parsed.secret, key.secret_hash)
        ):
            raise UNAUTHORIZED
        key.last_used_at = datetime.now(UTC)
        name, scopes = key.name, tuple(key.scopes)
    return TenantContext(
        tenant_id=parsed.tenant_id,
        key_id=parsed.key_id,
        key_name=name,
        actor=f"llave:{name}",
        scopes=scopes,
        client_ip=_client_ip(request),
        _factory=state.sessions,
    )


Tenant = Annotated[TenantContext, Depends(current_tenant)]
State = Annotated[AppState, Depends(get_state)]
