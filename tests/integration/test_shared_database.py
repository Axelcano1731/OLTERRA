"""Olterra en una base COMPARTIDA (Supabase: ISPWatch y Converza ya viven ahí).

Las tablas van a un esquema propio (``olterra``), no a ``public``, y los roles de Olterra
solo ven ese esquema. El rol dueño y el de la aplicación traen ``search_path = olterra, public``
(``public`` queda para PostGIS), como lo deja el alta de la base compartida.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from tests.conftest import ADMIN_URL, ROOT, _bootstrap, _drop, _with_db

pytestmark = pytest.mark.postgres

TABLES = {
    "tenants",
    "tenant_keys",
    "api_keys",
    "credentials",
    "tunnel_routers",
    "olts",
    "onus",
    "plan_runs",
    "audit_log",
    "reconciliation_runs",
}


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _prepare(name: str) -> None:
    """Lo que hace el alta de la base compartida, con un tercero que ya tiene sus tablas."""
    conn = await asyncpg.connect(_dsn(_with_db(ADMIN_URL or "", name)))
    try:
        await conn.execute("CREATE SCHEMA olterra AUTHORIZATION olterra_owner")
        await conn.execute("REVOKE ALL ON SCHEMA olterra FROM PUBLIC")
        for role in ("olterra_owner", "olterra_app"):
            await conn.execute(
                f'ALTER ROLE {role} IN DATABASE "{name}" SET search_path = olterra, public'
            )
        # Una tabla de otro sistema con el mismo nombre que una de Olterra: no debe tocarse.
        await conn.execute("CREATE TABLE public.tenants (id int PRIMARY KEY, nombre text)")
        await conn.execute("INSERT INTO public.tenants VALUES (1, 'de otro sistema')")
    finally:
        await conn.close()


async def _inspect(owner_url: str, app_url: str, admin_url: str) -> dict[str, object]:
    owner = await asyncpg.connect(_dsn(owner_url))
    app = await asyncpg.connect(_dsn(app_url))
    admin = await asyncpg.connect(_dsn(admin_url))
    try:
        in_olterra = {
            r[0]
            for r in await owner.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'olterra'"
            )
        }
        # La tabla ajena la lee el administrador: los roles de Olterra no tienen permiso (abajo).
        foreign = await admin.fetchval("SELECT nombre FROM public.tenants WHERE id = 1")
        return {
            "tables": in_olterra,
            "foreign_untouched": foreign == "de otro sistema",
            "search_path_app": await app.fetchval("SHOW search_path"),
            "app_sees_schema": await app.fetchval(
                "SELECT has_schema_privilege('olterra_app', 'olterra', 'USAGE')"
            ),
            # Con RLS y sin tenant fijado no se ve nada: es la tabla de Olterra, no la ajena.
            "app_tenants_rows": await app.fetchval("SELECT count(*) FROM tenants"),
            "app_reads_foreign": await app.fetchval(
                "SELECT has_table_privilege('olterra_app', 'public.tenants', 'SELECT')"
            ),
            "owner_reads_foreign": await owner.fetchval(
                "SELECT has_table_privilege('olterra_owner', 'public.tenants', 'SELECT')"
            ),
            "owner_version_in_schema": await owner.fetchval(
                "SELECT count(*) FROM olterra.alembic_version"
            ),
        }
    finally:
        await owner.close()
        await app.close()
        await admin.close()


def test_migrations_live_in_their_own_schema_of_a_shared_database() -> None:
    assert ADMIN_URL
    name = f"olterra_shared_{uuid.uuid4().hex[:10]}"
    asyncio.run(_bootstrap(ADMIN_URL, name))
    try:
        asyncio.run(_prepare(name))
        owner_url = _with_db(ADMIN_URL, name, "olterra_owner", "olterra_owner")
        app_url = _with_db(ADMIN_URL, name, "olterra_app", "olterra_app")
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", owner_url)
        command.upgrade(config, "head")

        admin_url = _with_db(ADMIN_URL, name)
        found = asyncio.run(_inspect(owner_url, app_url, admin_url))
        assert found["tables"] >= TABLES  # type: ignore[operator]
        assert found["foreign_untouched"], "la tabla ajena public.tenants no se toca"
        assert found["search_path_app"] == "olterra, public"
        assert found["app_sees_schema"] is True
        assert found["app_tenants_rows"] == 0
        assert found["app_reads_foreign"] is False, "olterra_app no lee tablas de otros sistemas"
        assert found["owner_reads_foreign"] is False, "ni olterra_owner (aunque ignore RLS)"
        assert found["owner_version_in_schema"] == 1  # alembic_version también en su esquema
    finally:
        asyncio.run(_drop(ADMIN_URL, name))
