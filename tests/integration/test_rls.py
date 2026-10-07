"""Aislamiento por tenant en PostgreSQL real: la base vuelve a filtrar lo que la API filtró."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from olterra.admin import create_api_key, create_tenant
from olterra.db.models import (
    ApiKey,
    AuditLog,
    Olt,
    ProvisionJob,
    ProvisionTemplate,
    Tenant,
    TunnelRouter,
    User,
    UserSession,
)
from olterra.db.session import create_engine, session_factory, tenant_session
from olterra.security.vault import Vault
from tests.conftest import PgDatabase

pytestmark = pytest.mark.postgres


@dataclass
class World:
    app: async_sessionmaker[AsyncSession]
    owner: async_sessionmaker[AsyncSession]
    a: UUID
    b: UUID
    router_b: UUID


@pytest.fixture
async def world(pg: PgDatabase, master_key: bytes) -> AsyncIterator[World]:
    owner_engine, app_engine = create_engine(pg.owner_url), create_engine(pg.app_url)
    owner, app = session_factory(owner_engine), session_factory(app_engine)
    vault = Vault.single(master_key)
    a = await create_tenant(owner, vault, slug=f"a-{uuid4().hex[:8]}", name="ISP A")
    b = await create_tenant(owner, vault, slug=f"b-{uuid4().hex[:8]}", name="ISP B")
    router_b = uuid4()
    async with tenant_session(app, b.id) as session:
        session.add(
            TunnelRouter(
                id=router_b,
                tenant_id=b.id,
                name="BNG",
                peer_index=1,
                overlay_ip=f"198.18.{b.net_index % 250}.{b.net_index % 200 + 1}",
                wg_public_key="x",
            )
        )
        session.add(Olt(tenant_id=b.id, name="OLT-B", nat_ip=f"198.19.200.{b.net_index % 250}"))
    async with tenant_session(app, a.id) as session:
        session.add(Olt(tenant_id=a.id, name="OLT-A"))
    yield World(app=app, owner=owner, a=a.id, b=b.id, router_b=router_b)
    await owner_engine.dispose()
    await app_engine.dispose()


async def test_app_only_sees_its_tenant(world: World) -> None:
    async with tenant_session(world.app, world.a) as session:
        names = (await session.execute(select(Olt.name))).scalars().all()
        tenants = (await session.execute(select(Tenant.id))).scalars().all()
    assert names == ["OLT-A"]
    assert tenants == [world.a]


async def test_without_tenant_nothing_is_visible(world: World) -> None:
    async with world.app() as session:
        assert (await session.execute(select(Olt))).scalars().all() == []
        assert (await session.execute(text("SELECT olterra_current_tenant()"))).scalar() is None


async def test_cannot_write_rows_for_another_tenant(world: World) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(world.app, world.a) as session:
            session.add(Olt(tenant_id=world.b, name="intrusa"))


async def test_cannot_move_a_row_to_another_tenant(world: World) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(world.app, world.a) as session:
            await session.execute(update(Olt).where(Olt.name == "OLT-A").values(tenant_id=world.b))


async def test_provision_templates_are_isolated(world: World) -> None:
    body = {"auth_profile": "default"}
    async with tenant_session(world.app, world.b) as session:
        session.add(ProvisionTemplate(tenant_id=world.b, name="Plan B", body=body))
    async with tenant_session(world.app, world.a) as session:
        session.add(ProvisionTemplate(tenant_id=world.a, name="Plan B", body=body))  # mismo nombre
    async with tenant_session(world.app, world.a) as session:
        rows = (await session.execute(select(ProvisionTemplate.tenant_id))).scalars().all()
    assert rows == [world.a]
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(world.app, world.a) as session:
            session.add(ProvisionTemplate(tenant_id=world.b, name="intrusa", body=body))


async def test_users_sessions_and_provision_jobs_are_isolated(world: World) -> None:
    from datetime import UTC, datetime, timedelta

    name = f"user{uuid4().hex[:8]}"
    # Los usuarios los crea olterra-admin (rol dueño): la API solo los lee y cambia contadores.
    async with tenant_session(world.owner, world.b) as session:
        user = User(tenant_id=world.b, username=name, display_name="B", password_hash="scrypt$x")
        session.add(user)
        await session.flush()
        user_id = user.id
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.app, world.b) as session:
            session.add(
                User(tenant_id=world.b, username=f"x{name}", display_name="X", password_hash="h")
            )

    async with tenant_session(world.app, world.b) as session:
        olt_b = (await session.execute(select(Olt.id).where(Olt.name == "OLT-B"))).scalar_one()
        session.add(
            UserSession(
                id=uuid4(),
                tenant_id=world.b,
                user_id=user_id,
                secret_hash=b"h",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        job = ProvisionJob(
            tenant_id=world.b,
            olt_id=olt_b,
            kind="authorize",
            step="discover",
            template={},
            request={"pon": 1},
            requested_by="prueba",
            next_run_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session.add(job)
        await session.flush()
        job_id = job.id

    async with tenant_session(world.app, world.a) as session:
        assert (await session.execute(select(User.id))).all() == []
        assert (await session.execute(select(UserSession.id))).all() == []
        assert (await session.execute(select(ProvisionJob.id))).all() == []
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(world.app, world.a) as session:
            session.add(
                UserSession(
                    id=uuid4(),
                    tenant_id=world.b,
                    user_id=user_id,
                    secret_hash=b"h",
                    expires_at=datetime.now(UTC),
                )
            )
    # La sesión no puede apuntar a un usuario de otro tenant (FK compuesta).
    with pytest.raises(IntegrityError):
        async with tenant_session(world.app, world.a) as session:
            session.add(
                UserSession(
                    id=uuid4(),
                    tenant_id=world.a,
                    user_id=user_id,
                    secret_hash=b"h",
                    expires_at=datetime.now(UTC),
                )
            )
    # El reloj de los trabajos ve, sin tenant, solo identificadores de los que tocan.
    async with world.app() as session:
        due = (
            await session.execute(
                text("SELECT tenant_id, job_id FROM olterra_due_provision_jobs()")
            )
        ).all()
    assert (world.b, job_id) in [tuple(row) for row in due]


async def test_foreign_keys_cannot_point_to_another_tenant(world: World) -> None:
    # Una FK simple aceptaría el UUID del router de B aunque A no pueda verlo:
    # las FK compuestas (tenant_id, router_id) lo impiden.
    with pytest.raises(IntegrityError):
        async with tenant_session(world.app, world.a) as session:
            session.add(Olt(tenant_id=world.a, name="OLT-A2", router_id=world.router_b))


async def test_nat_ip_is_unique_across_tenants_even_if_invisible(world: World) -> None:
    async with tenant_session(world.app, world.b) as session:
        taken = (await session.execute(select(Olt.nat_ip).where(Olt.name == "OLT-B"))).scalar_one()
    with pytest.raises(IntegrityError):
        async with tenant_session(world.app, world.a) as session:
            session.add(Olt(tenant_id=world.a, name="OLT-A3", nat_ip=str(taken)))


async def test_audit_log_is_append_only(world: World) -> None:
    async with tenant_session(world.app, world.a) as session:
        session.add(AuditLog(tenant_id=world.a, actor="prueba", action="olt.create"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.app, world.a) as session:
            await session.execute(update(AuditLog).values(action="borrado"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.app, world.a) as session:
            await session.execute(text("DELETE FROM audit_log"))


async def test_app_cannot_mint_api_keys_or_touch_tenants(world: World) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.app, world.a) as session:
            session.add(ApiKey(tenant_id=world.a, name="propia", secret_hash=b"x" * 32))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.app, world.a) as session:
            await session.execute(update(Tenant).values(status="closed"))


async def test_api_key_lookup_respects_tenant(world: World) -> None:
    from olterra.security import apikeys

    token = await create_api_key(world.owner, world.a, name="integracion")
    parsed = apikeys.parse(token)
    async with tenant_session(world.app, world.a) as session:
        assert await session.get(ApiKey, parsed.key_id) is not None
    async with tenant_session(world.app, world.b) as session:  # misma llave, otro tenant
        assert await session.get(ApiKey, parsed.key_id) is None


async def test_net_index_comes_from_sequence(world: World) -> None:
    async with world.owner() as session:
        indexes = (
            (
                await session.execute(
                    select(Tenant.net_index).where(Tenant.id.in_([world.a, world.b]))
                )
            )
            .scalars()
            .all()
        )
    assert len(set(indexes)) == 2 and all(i >= 1 for i in indexes)
