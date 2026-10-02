"""Transporte SSH real (asyncssh) para ``CliSession``."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

import asyncssh

from olterra.executor.cli import CliError


@dataclass
class SshConnectOptions:
    host: str
    port: int
    username: str
    password: str
    known_host_key: str | None = None
    legacy_algorithms: bool = False
    connect_timeout: float = 15.0
    terminal_width: int = 512


class AsyncsshTransport:
    def __init__(
        self, conn: asyncssh.SSHClientConnection, process: asyncssh.SSHClientProcess[str]
    ) -> None:
        self._conn = conn
        self._process = process

    @property
    def host_key(self) -> str | None:
        """Llave pública del host en formato OpenSSH (para guardarla en el primer uso)."""
        key = self._conn.get_server_host_key()
        return key.export_public_key("openssh").decode("ascii").strip() if key else None

    async def write(self, data: str) -> None:
        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def read(self, timeout: float) -> str:
        try:
            data = await asyncio.wait_for(self._process.stdout.read(65536), timeout)
        except TimeoutError:
            return ""
        if data == "" and self._process.stdout.at_eof():
            raise EOFError("La OLT cerró la sesión SSH")
        return data

    async def close(self) -> None:
        self._process.close()
        self._conn.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._conn.wait_closed(), 5)


def _connect_kwargs(options: SshConnectOptions) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": options.host,
        "port": options.port,
        "username": options.username,
        "password": options.password,
        "client_keys": None,
        "agent_path": None,
        "preferred_auth": "password,keyboard-interactive",
        "connect_timeout": options.connect_timeout,
        "login_timeout": options.connect_timeout,
        "keepalive_interval": 30,
    }
    if options.known_host_key:
        kwargs["known_hosts"] = ([asyncssh.import_public_key(options.known_host_key)], [], [])
    else:
        # Confianza al primer uso: se acepta y se reporta la llave para fijarla después.
        kwargs["known_hosts"] = None
    if options.legacy_algorithms:
        # Listas completas en orden de preferencia: los algoritmos fuertes se siguen
        # eligiendo primero; los viejos solo si la OLT no ofrece nada mejor.
        from asyncssh import encryption, kex, mac, public_key

        kwargs["kex_algs"] = kex.get_kex_algs()
        kwargs["encryption_algs"] = encryption.get_encryption_algs()
        kwargs["mac_algs"] = mac.get_mac_algs()
        kwargs["server_host_key_algs"] = public_key.get_public_key_algs()
    return kwargs


async def open_ssh_transport(options: SshConnectOptions) -> AsyncsshTransport:
    try:
        conn = await asyncssh.connect(**_connect_kwargs(options))
    except asyncssh.HostKeyNotVerifiable as exc:
        raise CliError("La llave SSH de la OLT no coincide con la registrada") from exc
    except asyncssh.PermissionDenied as exc:
        raise CliError("La OLT rechazó usuario o clave SSH") from exc
    except (OSError, asyncssh.Error, TimeoutError) as exc:
        raise CliError(f"No se pudo abrir SSH contra {options.host}:{options.port}: {exc}") from exc
    try:
        process = await conn.create_process(
            term_type="vt100",
            term_size=(options.terminal_width, 200),
            encoding="utf-8",
            errors="replace",
        )
    except asyncssh.Error as exc:
        conn.close()
        raise CliError(f"La OLT no abrió una terminal interactiva: {exc}") from exc
    return AsyncsshTransport(conn, process)
