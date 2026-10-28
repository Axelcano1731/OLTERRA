"""Transporte Telnet para ``CliSession``.

Para las OLT que no aceptan a los usuarios de la CLI por SSH. Telnet viaja en claro, así que
solo se usa dentro del túnel: o por la IP NAT de la OLT, o desde el MikroTik del ISP (la
puerta: la conexión TCP sale del router por reenvío SSH). Rechaza toda opción que el servidor
proponga y hace el login de la CLI (``Login:`` y ``Password:``) antes de entregar la sesión.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Any

import asyncssh

from olterra.executor.cli import CliError
from olterra.executor.ssh import SshConnectOptions, connect_error

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
_LOGIN = re.compile(r"(?i)(login|username|user name)\s*:\s*$")
_PASSWORD = re.compile(r"(?i)password\s*:\s*$")
# Última línea después de la clave: un prompt de la CLI, o la OLT pidiendo otra vez usuario o
# clave (la rechazó).
_PROMPT_END = re.compile(r"[>#]\s*$")
_ASKS_AGAIN = re.compile(r"(?i)(login|username|user name|password)\s*:\s*$")


class TelnetTransport:
    # Un par de streams de asyncio, o el de un canal SSH del MikroTik (misma interfaz en bytes).
    def __init__(self, reader: Any, writer: Any) -> None:
        self._reader = reader
        self._writer = writer
        self._pending = b""
        # Lo leído al entrar que la sesión CLI todavía no vio (su primer prompt).
        self._unread = ""

    @property
    def host_key(self) -> None:
        return None

    def _strip_iac(self, data: bytes) -> bytes:
        data = self._pending + data
        self._pending = b""
        out = bytearray()
        i = 0
        while i < len(data):
            byte = data[i]
            if byte != IAC:
                out.append(byte)
                i += 1
                continue
            if i + 1 >= len(data):
                self._pending = data[i:]
                break
            command = data[i + 1]
            if command in (DO, DONT, WILL, WONT):
                if i + 2 >= len(data):
                    self._pending = data[i:]
                    break
                answer = WONT if command in (DO, DONT) else DONT
                self._writer.write(bytes([IAC, answer, data[i + 2]]))
                i += 3
            elif command == SB:
                end = data.find(bytes([IAC, SE]), i)
                if end < 0:
                    self._pending = data[i:]
                    break
                i = end + 2
            elif command == IAC:
                out.append(IAC)
                i += 2
            else:
                i += 2
        return bytes(out)

    async def write(self, data: str) -> None:
        # Telnet espera CR LF al final de cada línea.
        self._writer.write(data.replace("\n", "\r\n").encode("utf-8"))
        await self._writer.drain()

    async def read(self, timeout: float) -> str:
        if self._unread:
            text, self._unread = self._unread, ""
            return text
        try:
            data = await asyncio.wait_for(self._reader.read(65536), timeout)
        except TimeoutError:
            return ""
        if not data:
            raise EOFError("La OLT cerró la sesión Telnet")
        return self._strip_iac(data).decode("utf-8", errors="replace")

    async def close(self) -> None:
        self._writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._writer.wait_closed(), 5)

    async def _read_until(self, pattern: re.Pattern[str], timeout: float) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        text = ""
        while loop.time() < deadline:
            text += await self.read(min(0.5, max(0.05, deadline - loop.time())))
            if pattern.search(text.rstrip("\r\n ") + " ") or pattern.search(text):
                return text
        raise CliError("La OLT no pidió usuario o clave por Telnet a tiempo")


async def open_telnet_transport(options: SshConnectOptions) -> TelnetTransport:
    try:
        if options.tunnel is not None:
            # Desde el MikroTik del ISP: la conexión TCP a la OLT la abre el router.
            opening: Any = options.tunnel.open_connection(options.host, options.port)
        else:
            opening = asyncio.open_connection(options.host, options.port)
        reader, writer = await asyncio.wait_for(opening, options.connect_timeout)
    except (OSError, TimeoutError, asyncssh.Error) as exc:
        raise connect_error("Telnet", options, exc) from exc
    transport = TelnetTransport(reader, writer)
    try:
        await transport._read_until(_LOGIN, options.connect_timeout)
        await transport.write(options.username + "\n")
        await transport._read_until(_PASSWORD, options.connect_timeout)
        await transport.write(options.password + "\n")
        transport._unread = await _after_password(transport, options.connect_timeout)
    except EOFError as exc:
        await transport.close()
        raise CliError("La OLT rechazó usuario o clave por Telnet") from exc
    except CliError:
        await transport.close()
        raise
    return transport


async def _after_password(transport: TelnetTransport, timeout: float) -> str:
    """Espera el primer prompt; si la OLT vuelve a pedir usuario o clave, la rechazó."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    text = ""
    while loop.time() < deadline:
        text += await transport.read(min(0.5, max(0.05, deadline - loop.time())))
        last = text.rstrip("\r\n").rsplit("\n", 1)[-1]
        if _ASKS_AGAIN.search(last):
            raise CliError("La OLT rechazó usuario o clave por Telnet")
        if _PROMPT_END.search(last):
            return text
    raise CliError("La OLT no devolvió el prompt después de entrar por Telnet")
