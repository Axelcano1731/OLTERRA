"""Entrar con usuario y contraseña, salir y cambiar la contraseña.

- El usuario es único en toda la plataforma: quien entra no tiene que saber el "slug" de su ISP.
  ``olterra_login_lookup`` (SECURITY DEFINER) traduce el nombre a (tenant, id); todo lo demás se
  lee con el tenant fijado y bajo RLS.
- Mismo mensaje para usuario inexistente y contraseña equivocada, y el mismo tiempo de respuesta
  (se compara contra un hash de mentira).
- Cinco intentos fallidos bloquean la cuenta 15 minutos; por IP hay un tope en memoria contra
  quien prueba muchas cuentas.
- La contraseña inicial (la que pone ``olterra-admin``) obliga a cambiarla al entrar.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text, update

from olterra.api.deps import State, Tenant, _client_ip
from olterra.api.schemas import LoginIn, LoginOut, PasswordChangeIn
from olterra.api.state import audit
from olterra.db.models import User, UserSession
from olterra.db.session import tenant_session
from olterra.security import passwords, sessions

router = APIRouter(prefix="/v1/auth", tags=["Plataforma"])

MAX_FAILED = 5
LOCK_FOR = timedelta(minutes=15)
SESSION_HOURS = 12
REMEMBER_DAYS = 30
_IP_WINDOW_S = 600
_IP_MAX_FAILURES = 20
_failures_by_ip: dict[str, deque[float]] = defaultdict(deque)

BAD_LOGIN = "Usuario o contraseña incorrectos"


def _ip_blocked(ip: str | None) -> bool:
    if ip is None:
        return False
    window = _failures_by_ip[ip]
    limit = time.monotonic() - _IP_WINDOW_S
    while window and window[0] < limit:
        window.popleft()
    return len(window) >= _IP_MAX_FAILURES


def _ip_failed(ip: str | None) -> None:
    if ip is not None:
        _failures_by_ip[ip].append(time.monotonic())


@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request, state: State) -> LoginOut:
    ip = _client_ip(request)
    if _ip_blocked(ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Demasiados intentos desde aquí: espera unos minutos"
        )
    async with state.sessions() as lookup:
        found = (
            await lookup.execute(
                text("SELECT tenant_id, user_id FROM olterra_login_lookup(:username)"),
                {"username": body.username.strip()},
            )
        ).first()
    if found is None:
        passwords.verify_password(body.password.get_secret_value(), passwords.DUMMY_HASH)
        _ip_failed(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)

    tenant_id, user_id = found
    now = datetime.now(UTC)
    outcome: str
    token: str | None = None
    must_change = False
    expires = now + (
        timedelta(days=REMEMBER_DAYS) if body.remember else timedelta(hours=SESSION_HOURS)
    )
    async with tenant_session(state.sessions, tenant_id) as session:
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            outcome = "fail"
        elif user.locked_until is not None and user.locked_until > now:
            outcome = "locked"
        elif not passwords.verify_password(body.password.get_secret_value(), user.password_hash):
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED:
                user.failed_logins = 0
                user.locked_until = now + LOCK_FOR
            outcome = "fail"
            await audit(
                session,
                tenant_id=tenant_id,
                actor=f"usuario:{user.username}",
                action="auth.login_failed",
                target_type="user",
                target_id=str(user.id),
                after={"bloqueado": user.locked_until is not None and user.locked_until > now},
                source_ip=ip,
            )
        else:
            outcome = "ok"
            user.failed_logins = 0
            user.locked_until = None
            user.last_login_at = now
            if passwords.needs_rehash(user.password_hash):
                user.password_hash = passwords.hash_password(body.password.get_secret_value())
            session_id = uuid4()
            token, secret_hash = sessions.generate(tenant_id, session_id)
            session.add(
                UserSession(
                    id=session_id,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    secret_hash=secret_hash,
                    expires_at=expires,
                    source_ip=ip,
                )
            )
            await audit(
                session,
                tenant_id=tenant_id,
                actor=f"usuario:{user.username}",
                action="auth.login",
                target_type="user",
                target_id=str(user.id),
                source_ip=ip,
            )
            must_change = user.must_change_password
    # Fuera de la transacción: los contadores de intentos quedan guardados aunque se responda 401.
    if outcome == "locked":
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "La cuenta quedó bloqueada 15 minutos por intentos fallidos",
        )
    if outcome != "ok" or token is None:
        _ip_failed(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
    return LoginOut(token=token, expires_at=expires, must_change_password=must_change)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(ctx: Tenant) -> None:
    if ctx.session_id is None:
        return  # una llave de API no tiene sesión que cerrar
    async with ctx.session() as session:
        row = await session.get(UserSession, ctx.session_id)
        if row is not None and row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordChangeIn, ctx: Tenant) -> None:
    if ctx.user_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Esto es para usuarios, no para llaves")
    async with ctx.session() as session:
        user = await session.get(User, ctx.user_id, with_for_update=True)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
        current = body.current_password.get_secret_value()
        new = body.new_password.get_secret_value()
        if not passwords.verify_password(current, user.password_hash):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "La contraseña actual no es esa")
        problem = passwords.password_problem(new, user.username)
        if problem is None and new == current:
            problem = "La contraseña nueva tiene que ser distinta de la actual"
        if problem is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
        user.password_hash = passwords.hash_password(new)
        user.must_change_password = False
        # Las demás sesiones de ese usuario quedan cerradas; la de quien la cambió sigue.
        await session.execute(
            update(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.id != ctx.session_id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action="auth.password_changed",
            target_type="user",
            target_id=str(user.id),
            source_ip=ctx.client_ip,
        )
