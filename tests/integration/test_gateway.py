"""El MikroTik del ISP como puerta: el ejecutor entra a él y desde ahí abre la OLT.

Un servidor SSH de prueba hace de MikroTik (usuario ``olterra`` y reenvío de conexiones, como
lo deja el script de Olterra) delante del simulador de VSOL, por SSH y por Telnet.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4

import asyncssh
import pytest
from pydantic import SecretStr

from olterra.devtools.vsol_sim import VsolSimulator
from olterra.drivers import get_driver
from olterra.drivers.base import CommandCall
from olterra.drivers.vsol_gpon.parsers import parse_onu_list
from olterra.executor.plan import Credential, Gateway, Plan, PlanResult, Priority, Target
from olterra.executor.runner import PlanRunner

DRIVER = get_driver("vsol-gpon")
GATEWAY_PASSWORD = "Puerta-olterra-77"


@dataclass
class FakeMikrotik:
    """Lo mínimo del SSH de RouterOS que usa Olterra: login y reenvío (direct-tcpip)."""

    password: str = GATEWAY_PASSWORD
    forwarding: bool = True
    forwarded: list[tuple[str, int]] = field(default_factory=list)
    server: asyncssh.SSHAcceptor | None = None

    async def start(self) -> int:
        mikrotik = self

        class Server(asyncssh.SSHServer):
            def begin_auth(self, username: str) -> bool:
                return True

            def password_auth_supported(self) -> bool:
                return True

            def validate_password(self, username: str, password: str) -> bool:
                return username == "olterra" and password == mikrotik.password

            def connection_requested(
                self, dest_host: str, dest_port: int, orig_host: str, orig_port: int
            ) -> bool:
                if not mikrotik.forwarding:
                    raise asyncssh.ChannelOpenError(
                        asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED, "forwarding disabled"
                    )
                mikrotik.forwarded.append((dest_host, dest_port))
                return True

        self.server = await asyncssh.create_server(
            Server,
            "127.0.0.1",
            0,
            server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        )
        port: int = self.server.sockets[0].getsockname()[1]
        return port

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


@pytest.fixture
async def olt() -> AsyncIterator[tuple[VsolSimulator, int, int]]:
    sim = VsolSimulator()
    ssh_port = await sim.start()
    telnet_port = await sim.start_telnet()
    yield sim, ssh_port, telnet_port
    await sim.stop()


@pytest.fixture
async def mikrotik() -> AsyncIterator[tuple[FakeMikrotik, int]]:
    router = FakeMikrotik()
    port = await router.start()
    yield router, port
    await router.stop()


def plan_through(gateway_port: int, **target: object) -> Plan:
    return Plan(
        tenant_id=uuid4(),
        olt_id=uuid4(),
        priority=Priority.USER,
        on_error="continue",
        target=Target(
            host="127.0.0.1",
            via=Gateway(host="127.0.0.1", port=gateway_port, name="BNG-PRUEBA"),
            **target,  # type: ignore[arg-type]
        ),
        session=DRIVER.session,
        steps=DRIVER.build_cli_steps([CommandCall("onu.list", {"pon": 1})]),
    )


def credential(gateway_password: str | None = GATEWAY_PASSWORD) -> Credential:
    return Credential(
        username="admin",
        password=SecretStr("olterra-sim"),
        gateway_password=SecretStr(gateway_password) if gateway_password else None,
    )


def onus(result: PlanResult) -> int:
    output = next(s.output for s in result.steps if s.output and "Onuindex" in s.output)
    return len(parse_onu_list(output or "", 1))


async def test_ssh_olt_through_the_mikrotik(
    olt: tuple[VsolSimulator, int, int], mikrotik: tuple[FakeMikrotik, int]
) -> None:
    _, ssh_port, _ = olt
    router, gateway_port = mikrotik
    result = await PlanRunner("prueba").run(
        plan_through(gateway_port, ssh_port=ssh_port), credential()
    )
    assert result.status == "ok", result
    assert onus(result) == 3
    # La conexión a la OLT la abrió el MikroTik, hacia la IP real y el puerto de la OLT.
    assert router.forwarded == [("127.0.0.1", ssh_port)]


async def test_telnet_olt_through_the_mikrotik(
    olt: tuple[VsolSimulator, int, int], mikrotik: tuple[FakeMikrotik, int]
) -> None:
    _, _, telnet_port = olt
    router, gateway_port = mikrotik
    plan = plan_through(gateway_port, cli_protocol="telnet", telnet_port=telnet_port)
    result = await PlanRunner("prueba").run(plan, credential())
    assert result.status == "ok", result
    assert onus(result) == 3
    assert router.forwarded == [("127.0.0.1", telnet_port)]


async def test_mikrotik_rejects_olterra_user(mikrotik: tuple[FakeMikrotik, int]) -> None:
    _, gateway_port = mikrotik
    result = await PlanRunner("prueba").run(
        plan_through(gateway_port, ssh_port=22), credential("clave-vieja-123")
    )
    assert result.status == "failed"
    error = result.steps[0].error or ""
    assert "MikroTik BNG-PRUEBA" in error and "rechazó al usuario de Olterra" in error
    assert "clave-vieja-123" not in result.model_dump_json()


async def test_mikrotik_without_forwarding(
    olt: tuple[VsolSimulator, int, int], mikrotik: tuple[FakeMikrotik, int]
) -> None:
    _, ssh_port, telnet_port = olt
    router, gateway_port = mikrotik
    router.forwarding = False
    for target in ({"ssh_port": ssh_port}, {"cli_protocol": "telnet", "telnet_port": telnet_port}):
        result = await PlanRunner("prueba").run(plan_through(gateway_port, **target), credential())
        assert result.status == "failed"
        assert "no deja a Olterra abrir conexiones" in (result.steps[0].error or ""), target


async def test_olt_unreachable_from_the_mikrotik(mikrotik: tuple[FakeMikrotik, int]) -> None:
    _, gateway_port = mikrotik
    with socket.socket() as probe:  # un puerto libre: nadie escucha ahí
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    for target in ({"ssh_port": closed}, {"cli_protocol": "telnet", "telnet_port": closed}):
        result = await PlanRunner("prueba").run(plan_through(gateway_port, **target), credential())
        error = result.steps[0].error or ""
        assert result.status == "failed"
        assert f"no pudo conectarse con la OLT 127.0.0.1:{closed}" in error, target


async def test_plan_through_mikrotik_needs_its_password(mikrotik: tuple[FakeMikrotik, int]) -> None:
    _, gateway_port = mikrotik
    result = await PlanRunner("prueba").run(
        plan_through(gateway_port, ssh_port=22), credential(None)
    )
    assert result.status == "failed"
    assert "no trae la clave de Olterra" in (result.steps[0].error or "")
