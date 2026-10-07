"""``olterra-admin``: tareas de plataforma que NO hace la API.

Corre con el rol dueño (``OLTERRA_MIGRATIONS_DATABASE_URL``), que puede ver todos
los tenants. La API y el ejecutor nunca usan ese rol.

    olterra-admin generar-llave-maestra
    olterra-admin generar-llaves-ejecutor
    olterra-admin migrar
    olterra-admin crear-tenant --slug isp-piloto --nombre "ISP Piloto"
    olterra-admin crear-llave --tenant isp-piloto --nombre integracion
    olterra-admin crear-usuario --tenant isp-piloto --usuario Ana --nombre "Ana Pérez"
    olterra-admin restablecer-clave --usuario Ana
    olterra-admin concentrador --ip 198.18.0.1

La contraseña de ``crear-usuario`` y ``restablecer-clave`` se pide sin eco o se lee de la
entrada estándar (una línea), nunca de la línea de comandos. Es una contraseña inicial: el
usuario la tiene que cambiar al entrar.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import os
import re
import sys
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from olterra.config import ConfigError, get_settings
from olterra.db.models import ApiKey, Tenant, TenantKey, User
from olterra.db.session import create_engine, session_factory, tenant_session
from olterra.security import apikeys, passwords, sealed
from olterra.security.vault import Vault
from olterra.tunnel.routeros import ScriptError, render_hub_bootstrap

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,31}$")
ROLES = ("admin", "tecnico", "lectura")
MIN_INITIAL_PASSWORD = 6


class AdminError(ValueError):
    pass


async def create_tenant(
    factory: async_sessionmaker[AsyncSession], vault: Vault, *, slug: str, name: str
) -> Tenant:
    """Crea el tenant con su llave de cifrado (DEK) envuelta por la llave maestra."""
    if not _SLUG.fullmatch(slug):
        raise AdminError("El slug va en minúsculas, números y guiones (2 a 32)")
    tenant_id = uuid4()
    async with tenant_session(factory, tenant_id) as session:
        tenant = Tenant(id=tenant_id, slug=slug, name=name)
        session.add(tenant)
        await session.flush()
        _, wrapped = vault.new_tenant_key(tenant_id)
        session.add(TenantKey(tenant_id=tenant_id, wrapped_dek=wrapped))
        await session.flush()
        await session.refresh(tenant)
    return tenant


async def create_api_key(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    *,
    name: str,
    scopes: list[str] | None = None,
) -> str:
    """Devuelve la llave completa. Se muestra una sola vez: en la base queda su hash."""
    key_id = uuid4()
    token, secret_hash = apikeys.generate(tenant_id, key_id)
    async with tenant_session(factory, tenant_id) as session:
        session.add(
            ApiKey(
                id=key_id,
                tenant_id=tenant_id,
                name=name,
                secret_hash=secret_hash,
                scopes=scopes or ["*"],
            )
        )
    return token


async def tenant_by_slug(factory: async_sessionmaker[AsyncSession], slug: str) -> Tenant:
    async with factory() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()
    if tenant is None:
        raise AdminError(f"No existe el tenant '{slug}'")
    return tenant


def read_initial_password() -> str:
    """Sin eco si hay terminal; si no, la primera línea de la entrada estándar."""
    if sys.stdin.isatty():
        first = getpass.getpass("Contraseña inicial: ")
        if getpass.getpass("Repítela: ") != first:
            raise AdminError("Las dos contraseñas no coinciden")
        password = first
    else:
        # PowerShell antepone la marca BOM al pasar texto por la tubería: no es parte de la clave.
        password = sys.stdin.readline().rstrip("\r\n").lstrip("﻿")
    if len(password) < MIN_INITIAL_PASSWORD:
        raise AdminError(
            f"La contraseña inicial necesita al menos {MIN_INITIAL_PASSWORD} caracteres"
        )
    return password


async def create_user(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    *,
    username: str,
    display_name: str,
    password: str,
    role: str = "admin",
    must_change_password: bool = True,
) -> User:
    if not _USERNAME.fullmatch(username):
        raise AdminError("Usuario de 3 a 32 caracteres: letras, números, punto, guion o guion bajo")
    if role not in ROLES:
        raise AdminError(f"Rol desconocido: {role} ({', '.join(ROLES)})")
    async with factory() as check:
        taken = await check.execute(
            select(User.id).where(func.lower(User.username) == username.lower())
        )
        if taken.first() is not None:
            raise AdminError(f"Ya existe un usuario {username}")
    async with tenant_session(factory, tenant_id) as session:
        user = User(
            tenant_id=tenant_id,
            username=username,
            display_name=display_name.strip() or username,
            password_hash=passwords.hash_password(password),
            role=role,
            must_change_password=must_change_password,
        )
        session.add(user)
        await session.flush()
        await session.refresh(user)
    return user


async def reset_password(
    factory: async_sessionmaker[AsyncSession], *, username: str, password: str
) -> User:
    """Contraseña inicial nueva (hay que cambiarla al entrar) y la cuenta desbloqueada."""
    async with factory() as session, session.begin():
        user = (
            await session.execute(select(User).where(func.lower(User.username) == username.lower()))
        ).scalar_one_or_none()
        if user is None:
            raise AdminError(f"No existe el usuario {username}")
        user.password_hash = passwords.hash_password(password)
        user.must_change_password = True
        user.failed_logins = 0
        user.locked_until = None
    return user


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    # En el repo, alembic.ini está en la raíz; en la imagen Docker, en /app (el directorio de trabajo).
    candidates = [Path.cwd() / "alembic.ini", Path(__file__).resolve().parents[2] / "alembic.ini"]
    ini = next((path for path in candidates if path.exists()), None)
    if ini is None:
        raise AdminError("No se encontró alembic.ini: corra el comando desde la raíz del proyecto")
    command.upgrade(Config(str(ini)), "head")


async def _with_owner(action: str, args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = create_engine(settings.effective_migrations_url(), pool_size=1, max_overflow=1)
    factory = session_factory(engine)
    try:
        if action == "crear-tenant":
            vault = Vault.single(settings.master_key_bytes(), settings.master_key_version)
            tenant = await create_tenant(factory, vault, slug=args.slug, name=args.nombre)
            print(f"Tenant {tenant.slug} creado: id={tenant.id} net_index={tenant.net_index}")
        elif action == "crear-llave":
            tenant = await tenant_by_slug(factory, args.tenant)
            token = await create_api_key(factory, tenant.id, name=args.nombre)
            print("Llave de API (se muestra una sola vez; guárdela en el gestor de secretos):")
            print(token)
        elif action == "crear-usuario":
            tenant = await tenant_by_slug(factory, args.tenant)
            user = await create_user(
                factory,
                tenant.id,
                username=args.usuario,
                display_name=args.nombre or args.usuario,
                password=read_initial_password(),
                role=args.rol,
            )
            print(f"Usuario {user.username} creado en {tenant.slug} (rol {user.role}).")
            print("La contraseña es inicial: se cambia la primera vez que entre.")
        elif action == "restablecer-clave":
            user = await reset_password(
                factory, username=args.usuario, password=read_initial_password()
            )
            print(f"Contraseña de {user.username} restablecida: la cambia al entrar.")
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="olterra-admin", description="Administración de la plataforma Olterra"
    )
    sub = parser.add_subparsers(dest="accion", required=True)
    sub.add_parser("generar-llave-maestra", help="Llave maestra de la bóveda (OLTERRA_MASTER_KEY)")
    sub.add_parser("generar-llaves-ejecutor", help="Par X25519 del ejecutor")
    sub.add_parser("migrar", help="Aplica las migraciones pendientes")
    tenant = sub.add_parser("crear-tenant")
    tenant.add_argument("--slug", required=True)
    tenant.add_argument("--nombre", required=True)
    key = sub.add_parser("crear-llave")
    key.add_argument("--tenant", required=True, help="slug del tenant")
    key.add_argument("--nombre", required=True)
    user = sub.add_parser("crear-usuario", help="Usuario para entrar a la interfaz")
    user.add_argument("--tenant", required=True, help="slug del tenant")
    user.add_argument("--usuario", required=True)
    user.add_argument("--nombre", help="Nombre para mostrar")
    user.add_argument("--rol", default="admin", choices=ROLES)
    reset = sub.add_parser("restablecer-clave", help="Contraseña inicial nueva para un usuario")
    reset.add_argument("--usuario", required=True)
    hub = sub.add_parser("concentrador", help="Script inicial del concentrador RouterOS")
    hub.add_argument(
        "--ip", default="198.18.0.1", help="IP del concentrador dentro del prefijo de la plataforma"
    )
    args = parser.parse_args(argv)

    try:
        if args.accion == "generar-llave-maestra":
            print(base64.b64encode(os.urandom(32)).decode())
        elif args.accion == "generar-llaves-ejecutor":
            private, public = sealed.generate_keypair()
            print(f"OLTERRA_EXECUTOR_PRIVATE_KEY={private}   # solo en el ejecutor")
            print(f"OLTERRA_EXECUTOR_PUBLIC_KEY={public}    # en la API")
        elif args.accion == "migrar":
            run_migrations()
        elif args.accion == "concentrador":
            settings = get_settings()
            print(
                render_hub_bootstrap(
                    listen_port=settings.tunnel_hub_port,
                    hub_address=IPv4Address(args.ip),
                    platform_prefix=IPv4Network(settings.tunnel_platform_prefix),
                    peer_pool=IPv4Network(settings.tunnel_peer_pool),
                    nat_pool=IPv4Network(settings.tunnel_nat_pool),
                    # SSTP para RouterOS v6: su certificado lleva la IP pública del concentrador.
                    sstp_port=settings.tunnel_sstp_port if settings.tunnel_hub_host else None,
                    sstp_host=settings.tunnel_hub_host,
                )
            )
        else:
            asyncio.run(_with_owner(args.accion, args))
    except (AdminError, ConfigError, ScriptError) as exc:
        raise SystemExit(f"Error: {exc}") from exc


if __name__ == "__main__":
    main()
