"""Entrar con usuario y contraseña: hash, cambio obligatorio, bloqueo, sesiones y aislamiento."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from olterra.admin import create_user
from olterra.db.models import AuditLog, User
from olterra.db.session import create_engine, session_factory, tenant_session
from tests.integration.test_api import Env, env  # noqa: F401 - el fixture se reutiliza

pytestmark = pytest.mark.postgres

INITIAL = "123456"
NEW = "Viota-Fibra-2026"


def unique(base: str = "AxelCano") -> str:
    return f"{base}{uuid4().hex[:6]}"


def make_user(env: Env, username: str, tenant: str = "a", **kw: Any) -> None:  # noqa: F811
    async def run() -> None:
        engine = create_engine(env.pg.owner_url)
        try:
            await create_user(
                session_factory(engine),
                env.tenant_a if tenant == "a" else env.tenant_b,
                username=username,
                display_name="Axel Cano",
                password=INITIAL,
                **kw,
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


def stored_user(env: Env, username: str) -> User:  # noqa: F811
    async def run() -> User:
        engine = create_engine(env.pg.owner_url)
        try:
            async with session_factory(engine)() as session:
                return (
                    await session.execute(select(User).where(User.username == username))
                ).scalar_one()
        finally:
            await engine.dispose()

    return asyncio.run(run())


def login(env: Env, username: str, password: str, **kw: Any) -> Any:  # noqa: F811
    return env.client.post(
        "/v1/auth/login", json={"username": username, "password": password, **kw}
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_password_is_hashed_and_first_login_forces_a_change(env: Env) -> None:  # noqa: F811
    name = unique()
    make_user(env, name)
    user = stored_user(env, name)
    assert user.password_hash.startswith("scrypt$") and INITIAL not in user.password_hash

    response = login(env, name.lower(), INITIAL)  # sin distinguir mayúsculas
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["must_change_password"] is True and body["token"].startswith("ols_")
    token = body["token"]

    me = env.client.get("/v1/me", headers=bearer(token)).json()
    assert me["user"]["username"] == name and me["user"]["must_change_password"]
    # Con la contraseña inicial no se puede hacer nada más que cambiarla.
    blocked = env.client.get("/v1/olts", headers=bearer(token))
    assert blocked.status_code == 403 and "Cambia tu contraseña" in blocked.text

    weak = env.client.post(
        "/v1/auth/password",
        json={"current_password": INITIAL, "new_password": "123456789"},
        headers=bearer(token),
    )
    assert weak.status_code == 400
    wrong = env.client.post(
        "/v1/auth/password",
        json={"current_password": "otra-cosa", "new_password": NEW},
        headers=bearer(token),
    )
    assert wrong.status_code == 400
    changed = env.client.post(
        "/v1/auth/password",
        json={"current_password": INITIAL, "new_password": NEW},
        headers=bearer(token),
    )
    assert changed.status_code == 204, changed.text
    assert env.client.get("/v1/olts", headers=bearer(token)).status_code == 200
    assert login(env, name, INITIAL).status_code == 401
    assert login(env, name, NEW).status_code == 200

    # Salir: el token deja de servir.
    assert env.client.post("/v1/auth/logout", headers=bearer(token)).status_code == 204
    assert env.client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_same_answer_for_unknown_user_and_wrong_password(env: Env) -> None:  # noqa: F811
    name = unique()
    make_user(env, name)
    unknown = login(env, unique("nadie"), INITIAL)
    wrong = login(env, name, "equivocada")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_five_failures_lock_the_account(env: Env) -> None:  # noqa: F811
    name = unique()
    make_user(env, name)
    for _ in range(5):
        assert login(env, name, "equivocada").status_code == 401
    locked = login(env, name, INITIAL)  # ni con la buena
    assert locked.status_code == 429 and "bloqueada" in locked.text
    user = stored_user(env, name)
    assert user.locked_until is not None

    async def audits() -> list[str]:
        engine = create_engine(env.pg.owner_url)
        try:
            async with session_factory(engine)() as session:
                rows = await session.execute(
                    select(AuditLog.action).where(
                        AuditLog.action.like("auth.%"), AuditLog.target_id == str(user.id)
                    )
                )
                return list(rows.scalars())
        finally:
            await engine.dispose()

    assert asyncio.run(audits()).count("auth.login_failed") == 5


def test_sessions_and_users_stay_inside_their_isp(env: Env) -> None:  # noqa: F811
    mine, other = unique(), unique("OtroIsp")
    make_user(env, mine, "a", must_change_password=False)
    make_user(env, other, "b", must_change_password=False)
    token = login(env, mine, INITIAL).json()["token"]
    assert env.client.get("/v1/me", headers=bearer(token)).json()["tenant"]["id"] == str(
        env.tenant_a
    )
    # Un token con el tenant cambiado no sirve: la sesión no aparece bajo RLS del otro tenant.
    forged = token.replace(env.tenant_a.hex, env.tenant_b.hex)
    assert env.client.get("/v1/me", headers=bearer(forged)).status_code == 401

    async def from_app_role() -> tuple[list[str], list[Any]]:
        engine = create_engine(env.pg.app_url)
        try:
            factory = session_factory(engine)
            async with tenant_session(factory, env.tenant_a) as session:
                names = list((await session.execute(select(User.username))).scalars())
            async with factory() as session:
                lookup = (
                    await session.execute(
                        text("SELECT * FROM olterra_login_lookup(:u)"), {"u": other.lower()}
                    )
                ).all()
            return names, lookup
        finally:
            await engine.dispose()

    names, lookup = asyncio.run(from_app_role())
    assert mine in names and other not in names  # no ve a los del otro ISP
    assert [tuple(row) for row in lookup] == [(env.tenant_b, stored_user(env, other).id)]
