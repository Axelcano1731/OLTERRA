"""CliSession contra una terminal de mentiras, guionada byte a byte."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable

import pytest

from olterra.drivers.vsol_gpon import SESSION
from olterra.drivers.vsol_gpon.commands import PASSWORD_QUESTION
from olterra.executor.cli import CliError, CliSession, CliTimeout, normalize_terminal_text


class ScriptedTransport:
    """Responde a cada escritura con los trozos que diga ``reply``."""

    def __init__(self, reply: Callable[[str], list[str]], banner: list[str]) -> None:
        self._reply = reply
        self._outgoing: deque[str] = deque(banner)
        self.written: list[str] = []
        self.closed = False

    async def write(self, data: str) -> None:
        self.written.append(data)
        self._outgoing.extend(self._reply(data))

    async def read(self, timeout: float) -> str:
        if self._outgoing:
            return self._outgoing.popleft()
        await asyncio.sleep(min(timeout, 0.01))
        return ""

    async def close(self) -> None:
        self.closed = True


def vsol_device(*, enable_password: str = "en4ble", pages: bool = False) -> ScriptedTransport:
    state = {"prompt": "gpon-olt>", "awaiting_password": False}

    def reply(data: str) -> list[str]:
        line = data.strip()
        if state["awaiting_password"]:
            state["awaiting_password"] = False
            if line == enable_password:
                state["prompt"] = "gpon-olt#"
                return ["\r\n", "gpon-olt#"]
            return ["\r\n% Bad passwords\r\n", "Password:"]
        echo = line + "\r\n"
        if line == "enable":
            state["awaiting_password"] = True
            return [echo, "Password:"]
        if line == "terminal length 0":
            return [echo, str(state["prompt"])]
        if line == "configure terminal":
            state["prompt"] = "gpon-olt(config)#"
            return [echo, "gpon-olt(con", "fig)#"]  # el prompt llega partido en dos lecturas
        if line == "show onu info" and pages:
            return [
                echo,
                "Onuindex  Sn\r\n----------------------\r\nGPON0/1:1 VSOL0008D09C\r\n",
                " --More-- ",
            ]
        if line == "" and pages:  # la barra espaciadora de la paginación
            return ["\r          \r", "GPON0/1:2 VSOL00A1B2C3\r\n", str(state["prompt"])]
        if line == "show color":
            return [echo, "\x1b[1;32mverde\x1b[0m y bor\x08\x08\x08bien\r\n", str(state["prompt"])]
        if line == "show slow":
            return [echo, "primera parte\r\n"]  # nunca vuelve el prompt
        return [echo, "% Unknown command.\r\n", str(state["prompt"])]

    return ScriptedTransport(reply, ["\r\nWelcome\r\n", "gpon-olt>"])


async def test_login_enable_and_run_command() -> None:
    device = vsol_device()
    session = CliSession(device, SESSION, read_chunk_timeout=0.05)
    banner = await session.login("en4ble")
    assert "Welcome" in banner
    assert session.prompt == "gpon-olt#"
    assert "terminal length 0\n" in device.written
    output = await session.run("configure terminal", timeout=2)
    assert output == ""
    assert session.prompt == "gpon-olt(config)#"


async def test_pager_is_answered_and_removed_from_output() -> None:
    device = vsol_device(pages=True)
    session = CliSession(device, SESSION, read_chunk_timeout=0.05)
    await session.login("en4ble")
    output = await session.run("show onu info", timeout=2)
    assert "More" not in output
    assert output.splitlines() == [
        "Onuindex  Sn",
        "----------------------",
        "GPON0/1:1 VSOL0008D09C",
        "GPON0/1:2 VSOL00A1B2C3",
    ]
    assert " " in device.written  # respondió a la paginación


async def test_ansi_and_backspaces_are_cleaned() -> None:
    session = CliSession(vsol_device(), SESSION, read_chunk_timeout=0.05)
    await session.login("en4ble")
    assert await session.run("show color", timeout=2) == "verde y bien"


async def test_cli_errors_are_detected() -> None:
    session = CliSession(vsol_device(), SESSION, read_chunk_timeout=0.05)
    await session.login("en4ble")
    output = await session.run("show inventada", timeout=2)
    assert session.find_error(output) == "% Unknown command."
    assert session.find_error("ONU 1 working") is None


async def test_timeout_returns_partial_output() -> None:
    session = CliSession(vsol_device(), SESSION, read_chunk_timeout=0.02)
    await session.login("en4ble")
    with pytest.raises(CliTimeout) as info:
        await session.run("show slow", timeout=0.3)
    assert info.value.partial_output == "primera parte"


async def test_wrong_enable_password_fails_fast() -> None:
    session = CliSession(vsol_device(), SESSION, read_chunk_timeout=0.05)
    with pytest.raises(CliError, match="enable"):
        await session.login("equivocada")


async def test_multiline_commands_are_refused() -> None:
    session = CliSession(vsol_device(), SESSION, read_chunk_timeout=0.05)
    await session.login("en4ble")
    with pytest.raises(CliError):
        await session.run("show onu info\nno onu 1", timeout=1)


QUESTION = PASSWORD_QUESTION


def password_device(*, repeat: bool = False) -> ScriptedTransport:
    """Pregunta la clave nueva y la confirmación DESPUÉS del Enter, como la V1600G0-B."""
    state = {"prompt": "gpon-olt#", "asked": 0}

    def reply(data: str) -> list[str]:
        line = data.strip()
        if state["asked"] == 1 or (repeat and state["asked"] > 1):
            state["asked"] += 1
            return ["\r\n", "Confirm password:"]
        if state["asked"] == 2:
            state["asked"] = 0
            # El aviso llega partido: sin ":" final no debe tomarse por otra pregunta.
            return ["\r\nPassword changed", " successfully\r\n", str(state["prompt"])]
        echo = line + "\r\n"
        if line == "enable":
            return [echo, "Password:"] if state["prompt"] == "gpon-olt>" else [echo, "gpon-olt#"]
        if line.startswith("user login-password"):
            state["asked"] = 1
            return [echo, "New pass", "word:"]  # la pregunta también llega partida
        return [echo, str(state["prompt"])]

    return ScriptedTransport(reply, ["gpon-olt#"])


async def test_questions_in_the_middle_of_a_command_are_answered() -> None:
    device = password_device()
    session = CliSession(device, SESSION, read_chunk_timeout=0.05)
    await session.login(None)
    output = await session.run(
        "user login-password admin", timeout=2, replies=[(QUESTION, "Nueva#2026", 2)]
    )
    assert device.written.count("Nueva#2026\n") == 2  # la clave nueva y su confirmación
    assert "Nueva#2026" not in output
    assert output.splitlines() == [
        "New password:",
        "Confirm password:",
        "Password changed successfully",
    ]
    assert session.prompt == "gpon-olt#"


async def test_a_question_that_keeps_coming_back_aborts_the_command() -> None:
    device = password_device(repeat=True)
    session = CliSession(device, SESSION, read_chunk_timeout=0.05)
    await session.login(None)
    with pytest.raises(CliError, match="volvió a preguntar"):
        await session.run(
            "user login-password admin", timeout=2, replies=[(QUESTION, "Nueva#2026", 2)]
        )
    assert device.written[-1] == "\x03"  # Ctrl-C: lo siguiente no se toma como respuesta


async def test_an_unexpected_question_times_out_without_answering() -> None:
    device = password_device()
    session = CliSession(device, SESSION, read_chunk_timeout=0.02)
    await session.login(None)
    with pytest.raises(CliTimeout) as info:
        await session.run("user login-password admin", timeout=0.3)
    assert info.value.partial_output == "New password:"
    assert device.written[-1] == "user login-password admin\n"


def test_password_question_pattern() -> None:
    import re

    asks = re.compile(QUESTION)
    for line in ("New password:", "Confirm Password: ", "Retype:", "Please input passwd:"):
        assert asks.search(line), line
    for line in ("Password changed successfully", "gpon-olt(config)#", "user login-password a"):
        assert not asks.search(line), line


def test_normalize_terminal_text() -> None:
    assert normalize_terminal_text("a\r\nb\r\n") == "a\nb\n"
    assert normalize_terminal_text(" --More-- \r          \rfila") == "fila"
    assert normalize_terminal_text("abc\x08\x08xy") == "axy"
