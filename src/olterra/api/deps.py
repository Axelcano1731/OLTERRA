"""Autenticación (llave de API o sesión de usuario) y contexto de tenant.

La llave o la sesión se verifica DENTRO de una transacción con el tenant fijado: si dice ser
del tenant A pero su id es de otro, RLS hace que no aparezca y la respuesta es 401. Cada
endpoint abre después sus propias transacciones con ``ctx.session()``, así controla cuándo
confirma y nada se confirma después de haber respondido.

Un usuario con la contraseña inicial (``must_change_password``) solo puede ver quién es,
cambiarla o salir.
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
from olterra.db.models import ApiKey, User, UserSession
from olterra.db.session import tenant_session
from olterra.security import apikeys, sessions

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="La sesión venció o la llave no es válida. Vuelve a entrar.",
    headers={"WWW-Authenticate": "Bearer"},
)

# Qué puede hacer cada rol de usuario (las llaves de API traen sus propios permisos).
ROLE_SCOPES: dict[str, tuple[str, ...]] = {
    "admin": ("*",),
    "tecnico": ("olt:read", "onu:write"),
    "lectura": ("olt:read",),
}
# Lo único permitido mientras no se cambie la contraseña inicial.
_FIRST_LOGIN_PATHS = ("/v1/me", "/v1/auth/password", "/v1/auth/logout")


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
    user_id: UUID | None = None
    session_id: UUID | None = None

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
    if sessions.looks_like_session(authorization[7:]):
        return await _user_session(request, state, authorization[7:])
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


async def _user_session(request: Request, state: AppState, token: str) -> TenantContext:
    try:
        parsed = sessions.parse(token)
    except sessions.InvalidSessionToken:
        raise UNAUTHORIZED from None
    now = datetime.now(UTC)
    async with tenant_session(state.sessions, parsed.tenant_id) as session:
        row = await session.get(UserSession, parsed.session_id)
        if (
            row is None
            or row.revoked_at is not None
            or row.expires_at <= now
            or not sessions.verify(parsed.secret, row.secret_hash)
        ):
            raise UNAUTHORIZED
        user = await session.get(User, row.user_id)
        if user is None or user.disabled_at is not None:
            raise UNAUTHORIZED
        row.last_used_at = now
        username, role, must_change = user.username, user.role, user.must_change_password
    if must_change and request.url.path not in _FIRST_LOGIN_PATHS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cambia tu contraseña inicial para seguir")
    return TenantContext(
        tenant_id=parsed.tenant_id,
        key_id=parsed.session_id,
        key_name=username,
        actor=f"usuario:{username}",
        scopes=ROLE_SCOPES.get(role, ()),
        client_ip=_client_ip(request),
        _factory=state.sessions,
        user_id=row.user_id,
        session_id=parsed.session_id,
    )


Tenant = Annotated[TenantContext, Depends(current_tenant)]
State = Annotated[AppState, Depends(get_state)]
