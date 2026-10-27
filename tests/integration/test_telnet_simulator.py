"""Ejecutor por Telnet contra el simulador (se presenta como la OLT de Tocaima, V1.1.9R)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from pydantic import SecretStr

from olterra.devtools.vsol_sim import VsolSimulator
from olterra.drivers import get_driver
from olterra.drivers.base import CommandCall
from olterra.drivers.vsol_gpon.parsers import parse_onu_list
from olterra.executor.plan import Credential, Plan, Priority, Target
from olterra.executor.runner import PlanRunner

DRIVER = get_driver("vsol-gpon")


@pytest.fixture
async def telnet() -> AsyncIterator[tuple[VsolSimulator, int]]:
    sim = VsolSimulator()
    port = await sim.start_telnet()
    yield sim, port
    await sim.stop()


def plan_for(port: int, calls: list[CommandCall]) -> Plan:
    return Plan(
        tenant_id=uuid4(),
        olt_id=uuid4(),
        priority=Priority.USER,
        on_error="continue",
        target=Target(host="127.0.0.1", cli_protocol="telnet", telnet_port=port, ssh_port=1),
        session=DRIVER.session,
        steps=DRIVER.build_cli_steps(calls),
    )


async def test_read_plan_over_telnet(telnet: tuple[VsolSimulator, int]) -> None:
    sim, port = telnet
    plan = plan_for(port, [CommandCall("system.version"), CommandCall("onu.list", {"pon": 1})])
    credential = Credential(username="admin", password=SecretStr("olterra-sim"))
    result = await PlanRunner("prueba").run(plan, credential)
    assert result.status == "ok", result
    assert result.host_key is None  # Telnet no tiene llave de host
    onu_list = next(s for s in result.steps if s.output and "Onuindex" in s.output)
    assert len(parse_onu_list(onu_list.output or "", 1)) == 3
    assert sim.commands_seen[:2] == ["enable", "terminal length 0"]


async def test_telnet_pagination_without_terminal_length(telnet: tuple[VsolSimulator, int]) -> None:
    sim, port = telnet
    sim.config.terminal_length_supported = False
    sim.config.page_lines = 2
    plan = plan_for(port, [CommandCall("onu.list", {"pon": 1})])
    result = await PlanRunner("prueba").run(
        plan, Credential(username="admin", password=SecretStr("olterra-sim"))
    )
    onu_step = result.steps[2]
    assert onu_step.ok and len(parse_onu_list(onu_step.output or "", 1)) == 3


async def test_telnet_wrong_password_fails_without_leaking_it(
    telnet: tuple[VsolSimulator, int],
) -> None:
    _, port = telnet
    plan = plan_for(port, [CommandCall("system.version")])
    started = time.monotonic()
    result = await PlanRunner("prueba").run(
        plan, Credential(username="admin", password=SecretStr("clave-mala-123"))
    )
    assert time.monotonic() - started < 5  # la OLT vuelve a pedir "Login:": no hay que esperar
    assert result.status == "failed"
    assert "rechazó usuario o clave" in (result.steps[0].error or "")
    assert "clave-mala-123" not in result.model_dump_json()
