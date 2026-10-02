"""Fixtures compartidas.

Las pruebas marcadas ``postgres`` necesitan ``OLTERRA_TEST_ADMIN_URL``: una URL de
superusuario (``postgresql://postgres:clave@localhost:5432/postgres``). Con ella se
crea una base nueva por corrida, con los roles reales y la migración aplicada, y se
borra al final. Sin la variable, esas pruebas se saltan.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("OLTERRA_TEST_ADMIN_URL")
NATS_URL = os.environ.get("OLTERRA_TEST_NATS_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "postgres" in item.keywords and not ADMIN_URL:
            item.add_marker(
                pytest.mark.skip(
                    reason="Defina OLTERRA_TEST_ADMIN_URL para correr pruebas con PostgreSQL"
                )
            )
        if "nats" in item.keywords and not NATS_URL:
            item.add_marker(
                pytest.mark.skip(reason="Defina OLTERRA_TEST_NATS_URL para correr pruebas con NATS")
            )


@pytest.fixture
def master_key() -> bytes:
    return os.urandom(32)


@dataclass(frozen=True)
class PgDatabase:
    name: str
    owner_url: str  # postgresql+asyncpg://olterra_owner:...
    app_url: str  # postgresql+asyncpg://olterra_app:...


def _with_db(url: str, database: str, user: str | None = None, password: str | None = None) -> str:
    """Cambia base (y opcionalmente usuario) de una URL postgresql://."""
    from sqlalchemy.engine import make_url

    parsed = make_url(url).set(database=database, drivername="postgresql+asyncpg")
    if user is not None:
        parsed = parsed.set(username=user, password=password)
    return parsed.render_as_string(hide_password=False)


async def _bootstrap(admin_url: str, database: str) -> None:
    import asyncpg

    admin = admin_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(admin)
    try:
        for role, flags in (("olterra_owner", "BYPASSRLS"), ("olterra_app", "NOBYPASSRLS")):
            if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role):
                await conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{role}' {flags}")
        await conn.execute(f'CREATE DATABASE "{database}" OWNER olterra_owner')
    finally:
        await conn.close()
    conn = await asyncpg.connect(
        _with_db(admin, database).replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    finally:
        await conn.close()


async def _drop(admin_url: str, database: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(admin_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", database
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def pg() -> Iterator[PgDatabase]:
    if not ADMIN_URL:
        pytest.skip("Sin OLTERRA_TEST_ADMIN_URL")
    from alembic import command
    from alembic.config import Config

    name = f"olterra_test_{uuid.uuid4().hex[:10]}"
    asyncio.run(_bootstrap(ADMIN_URL, name))
    database = PgDatabase(
        name=name,
        owner_url=_with_db(ADMIN_URL, name, "olterra_owner", "olterra_owner"),
        app_url=_with_db(ADMIN_URL, name, "olterra_app", "olterra_app"),
    )
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database.owner_url)
    try:
        command.upgrade(config, "head")
        yield database
    finally:
        asyncio.run(_drop(ADMIN_URL, name))


def b64key(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def ca_pem(*, ca: bool = True) -> str:
    """Certificado de prueba como el de la CA del concentrador (``ca=False``: uno que no lo es)."""
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Olterra CA prueba")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
