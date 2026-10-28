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
    # Conexión SSH ya abierta al MikroTik del ISP (la puerta): la de la OLT sale desde él.
    tunnel: asyncssh.SSHClientConnection | None = None
    # Nombre del MikroTik, para los errores.
    tunnel_name: str = ""


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
    if options.tunnel is not None:
        kwargs["tunnel"] = options.tunnel
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


def connect_error(protocol: str, options: SshConnectOptions, exc: BaseException) -> CliError:
    """Por qué no se abrió la conexión. Un TimeoutError viene sin texto: se dice qué pasó."""
    if options.tunnel is not None and isinstance(exc, asyncssh.ChannelOpenError):
        return forward_error(options, exc)
    reason = str(exc).strip()
    if isinstance(exc, TimeoutError) or not reason:
        reason = (
            f"no respondió en {options.connect_timeout:g} s; revisa que Olterra llegue a esa IP "
            "(si la OLT está en la red del ISP, va detrás de su router del túnel)"
        )
    return CliError(f"No se pudo abrir {protocol} contra {options.host}:{options.port}: {reason}")


def forward_error(options: SshConnectOptions, exc: asyncssh.ChannelOpenError) -> CliError:
    """El MikroTik no abrió la conexión hacia la OLT: o no deja reenviar, o la OLT no contesta."""
    router = f"El MikroTik {options.tunnel_name}".rstrip()
    if exc.code == asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED:
        return CliError(
            f"{router} no deja a Olterra abrir conexiones hacia su red: vuelve a importar en ese "
            "router el script de Olterra (Túnel > Rotar)"
        )
    reason = exc.reason.strip() or "sin respuesta"
    return CliError(
        f"{router} no pudo conectarse con la OLT {options.host}:{options.port} ({reason}): revisa "
        "que esa sea la IP de la OLT en la red del ISP, que el MikroTik la alcance y el puerto"
    )


async def open_gateway(options: SshConnectOptions) -> asyncssh.SSHClientConnection:
    """Entra al MikroTik del ISP con el usuario ``olterra``: por esa conexión salen las de las OLT.

    ``options.tunnel_name`` es el nombre del router, para los errores.
    """
    router = f"al MikroTik {options.tunnel_name}".rstrip()
    try:
        return await asyncssh.connect(**_connect_kwargs(options))
    except asyncssh.PermissionDenied as exc:
        raise CliError(
            f"No se pudo entrar {router}: rechazó al usuario de Olterra. Vuelve a importar en ese "
            "router el script de Olterra (Túnel > Rotar)"
        ) from exc
    except asyncssh.HostKeyNotVerifiable as exc:
        raise CliError(f"No se pudo entrar {router}: su llave SSH cambió") from exc
    except (OSError, asyncssh.Error, TimeoutError) as exc:
        reason = str(exc).strip()
        if isinstance(exc, TimeoutError) or not reason:
            reason = (
                f"no respondió en {options.connect_timeout:g} s; revisa que su túnel esté conectado "
                "(Túnel) y que su servicio SSH esté habilitado"
            )
        raise CliError(
            f"No se pudo entrar {router} ({options.host}:{options.port}): {reason}"
        ) from exc


async def open_ssh_transport(options: SshConnectOptions) -> AsyncsshTransport:
    try:
        conn = await asyncssh.connect(**_connect_kwargs(options))
    except asyncssh.HostKeyNotVerifiable as exc:
        raise CliError("La llave SSH de la OLT no coincide con la registrada") from exc
    except asyncssh.PermissionDenied as exc:
        raise CliError("La OLT rechazó usuario o clave SSH") from exc
    except (OSError, asyncssh.Error, TimeoutError) as exc:
        raise connect_error("SSH", options, exc) from exc
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
