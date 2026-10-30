"""Usuarios del ISP: el admin crea, cambia, deshabilita y restablece la contraseña de los suyos.

- Todo pasa con el tenant fijado y bajo RLS: un admin solo ve y toca los usuarios de su ISP.
- El usuario es único en toda la plataforma (``uq_users_username``): si el nombre ya existe en
  otro ISP, se responde lo mismo que si existiera en este.
- La contraseña inicial la genera Olterra, se muestra una sola vez y obliga a cambiarla al
  entrar (como la de ``olterra-admin``). Restablecerla o deshabilitar cierra sus sesiones.
- Un admin no puede quitarse el rol ni deshabilitarse, y el ISP nunca se queda sin un admin
  activo.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from olterra.api.deps import Tenant
from olterra.api.state import audit
from olterra.db.models import User, UserSession
from olterra.security import passwords

router = APIRouter(prefix="/v1/users", tags=["Usuarios"])

Role = Literal["admin", "tecnico", "lectura"]
# Sin letras ni números que se confundan al dictarla (0/O, 1/l/I).
_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class UserOut(BaseModel):
    id: UUID
    username: str
    display_name: str
    role: Role
    must_change_password: bool = Field(description="Todavía tiene la contraseña inicial")
    disabled: bool
    locked: bool = Field(description="Bloqueado 15 minutos por intentos fallidos")
    last_login_at: datetime | None
    created_at: datetime


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.\-]{3,32}$")
    display_name: str = Field("", max_length=64)
    role: Role = "tecnico"


class UserUpdate(BaseModel):
    """Cambia solo lo que viene."""

    display_name: str | None = Field(None, min_length=1, max_length=64)
    role: Role | None = None
    disabled: bool | None = None


class UserWithPassword(BaseModel):
    user: UserOut
    initial_password: str = Field(
        description="Se muestra una sola vez; hay que cambiarla al entrar"
    )


def initial_password() -> str:
    while True:
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(12))
        if passwords.password_problem(candidate, "") is None:
            return candidate


def _out(user: User) -> UserOut:
    now = datetime.now(UTC)
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,  # type: ignore[arg-type]
        must_change_password=user.must_change_password,
        disabled=user.disabled_at is not None,
        locked=user.locked_until is not None and user.locked_until > now,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


async def _get(session: Any, user_id: UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    return user


async def _revoke_sessions(session: Any, user_id: UUID) -> None:
    await session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def _active_admins(session: Any) -> int:
    count: int = (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.disabled_at.is_(None))
        )
    ).scalar_one()
    return count


@router.get("", response_model=list[UserOut])
async def list_users(ctx: Tenant) -> list[UserOut]:
    ctx.require("users:read")
    async with ctx.session() as session:
        rows = (await session.execute(select(User).order_by(func.lower(User.username)))).scalars()
        return [_out(user) for user in rows]


@router.post("", response_model=UserWithPassword, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, ctx: Tenant) -> UserWithPassword:
    ctx.require("users:write")
    password = initial_password()
    try:
        async with ctx.session() as session:
            user = User(
                tenant_id=ctx.tenant_id,
                username=body.username,
                display_name=body.display_name.strip() or body.username,
                password_hash=passwords.hash_password(password),
                role=body.role,
                must_change_password=True,
            )
            session.add(user)
            await session.flush()
            await session.refresh(user)
            await audit(
                session,
                tenant_id=ctx.tenant_id,
                actor=ctx.actor,
                action="user.create",
                target_type="user",
                target_id=str(user.id),
                after={"username": user.username, "role": user.role},
                source_ip=ctx.client_ip,
            )
            return UserWithPassword(user=_out(user), initial_password=password)
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"El usuario {body.username} ya existe: elige otro nombre"
        ) from exc


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: UUID, body: UserUpdate, ctx: Tenant) -> UserOut:
    ctx.require("users:write")
    async with ctx.session() as session:
        user = await _get(session, user_id)
        before = {
            "display_name": user.display_name,
            "role": user.role,
            "disabled": user.disabled_at is not None,
        }
        is_self = ctx.user_id == user.id
        losing_admin = user.role == "admin" and (
            (body.role is not None and body.role != "admin") or body.disabled is True
        )
        if losing_admin and is_self:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "No puedes quitarte el rol de admin ni deshabilitarte: pídeselo a otro admin",
            )
        if losing_admin and user.disabled_at is None and await _active_admins(session) <= 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "El ISP se quedaría sin un admin activo"
            )
        if body.display_name is not None:
            user.display_name = body.display_name.strip()
        if body.role is not None:
            user.role = body.role
        if body.disabled is True and user.disabled_at is None:
            user.disabled_at = datetime.now(UTC)
            await _revoke_sessions(session, user.id)
        elif body.disabled is False:
            user.disabled_at = None
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action="user.update",
            target_type="user",
            target_id=str(user.id),
            before=before,
            after={
                "display_name": user.display_name,
                "role": user.role,
                "disabled": user.disabled_at is not None,
            },
            source_ip=ctx.client_ip,
        )
        await session.flush()
        return _out(user)


@router.post("/{user_id}/password", response_model=UserWithPassword)
async def reset_password(user_id: UUID, ctx: Tenant) -> UserWithPassword:
    """Contraseña inicial nueva: la cuenta se desbloquea y se cierran sus sesiones."""
    ctx.require("users:write")
    password = initial_password()
    async with ctx.session() as session:
        user = await _get(session, user_id)
        user.password_hash = passwords.hash_password(password)
        user.must_change_password = True
        user.failed_logins = 0
        user.locked_until = None
        await _revoke_sessions(session, user.id)
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action="user.password_reset",
            target_type="user",
            target_id=str(user.id),
            source_ip=ctx.client_ip,
        )
        await session.flush()
        return UserWithPassword(user=_out(user), initial_password=password)
